# Обработчик контрагентов: поиск/создание в Мой Склад

import re

from woo_moysklad.exceptions import CounterpartyError
from woo_moysklad.logger import get_logger

log = get_logger(__name__)


def _has_letters(s: str) -> bool:
    """True, если в строке есть хотя бы одна буква (значит это имя, а не телефон)."""
    return any(ch.isalpha() for ch in s)


def _is_name_extension(existing: str, full: str) -> bool:
    """True, если full начинается с existing и длиннее (без учёта регистра).

    «Александр» ⊂ «Александр Лазарев» → True (дописали фамилию)
    «Алекс» ⊂ «Александр Иванов»      → True (имя уточнили и дописали)
    «Александр» vs «Алексей Иванов»   → False (другое имя — не трогаем)
    «Александра» vs «Александр Иванов» → False (existing длиннее совпадения)
    """
    ex = " ".join(existing.lower().split())
    fl = " ".join(full.lower().split())
    return bool(ex) and len(fl) > len(ex) and fl.startswith(ex)


def split_full_name(full_name: str) -> tuple[str, str, str]:
    """Разбить ФИО на (firstName, lastName, middleName).

    "Екатерина Кошенкова"   → ("Екатерина", "Кошенкова", "")
    "Иванов Иван Иванович"  → ("Иванов", "Иван", "Иванович")
    "Мария"                 → ("Мария", "", "")
    """
    parts = full_name.strip().split()
    if len(parts) == 0:
        return ("", "", "")
    elif len(parts) == 1:
        return (parts[0], "", "")
    elif len(parts) == 2:
        return (parts[0], parts[1], "")
    else:
        return (parts[0], parts[1], " ".join(parts[2:]))


def normalize_phone(phone: str) -> str:
    """Нормализовать телефон: убрать лишние символы, привести к формату +7XXXXXXXXXX.

    "+7 (909) 937-18-45" → "+79099371845"
    "89099371845"         → "+79099371845"
    "79099371845"         → "+79099371845"
    """
    if not phone:
        return phone

    # Убираем пробелы, скобки, дефисы
    cleaned = re.sub(r"[\s()\-]", "", phone)

    # 8XXXXXXXXXX → +7XXXXXXXXXX
    if cleaned.startswith("8") and len(cleaned) == 11:
        cleaned = "+7" + cleaned[1:]
    # 7XXXXXXXXXX (без +) → +7XXXXXXXXXX
    elif cleaned.startswith("7") and len(cleaned) == 11:
        cleaned = "+" + cleaned
    # Уже +7... — оставляем
    elif cleaned.startswith("+7"):
        pass

    # Проверяем формат результата
    if not (cleaned.startswith("+7") and len(cleaned) == 12):
        log.warning("Телефон не удалось нормализовать, используем как есть", phone=phone, cleaned=cleaned)
        return cleaned

    return cleaned


class CounterpartyHandler:
    """Поиск и создание контрагентов в Мой Склад по телефону из billing."""

    def __init__(self, ms_client):
        self.ms_client = ms_client

    def find_or_create_from_normalized(self, customer) -> dict:
        """Найти или создать контрагента из NormalizedCustomer."""
        billing = {
            "first_name": customer.full_name,
            "phone": customer.phone,
            "email": customer.email,
        }
        return self.find_or_create(billing)

    def _build_enrichment_patch(self, counterparty: dict, full_name: str, email: str) -> dict:
        """Поля для дозаполнения существующего контрагента данными из заказа.

        Имя — если у существующего оно «заглушка» (нет букв, обычно телефон) или
        пустое, ЛИБО если имя из заказа дополняет существующее («Александр» →
        «Александр Лазарев» — дописали фамилию). Иначе реальное имя НЕ трогаем.
        Email — только если у существующего пусто. Так не затираем правки менеджера.
        """
        patch: dict = {}

        existing_name = (counterparty.get("name") or "").strip()
        if full_name and _has_letters(full_name) and (
                not _has_letters(existing_name)
                or _is_name_extension(existing_name, full_name)):
            first, last, middle = split_full_name(full_name)
            patch["name"] = full_name
            patch["firstName"] = first
            patch["lastName"] = last
            patch["middleName"] = middle

        if email and not (counterparty.get("email") or "").strip():
            patch["email"] = email

        return patch

    def find_or_create(self, billing: dict) -> dict:
        """Найти или создать контрагента в МС. Возвращает meta-ссылку.

        billing = {"first_name": "Екатерина Кошенкова", "email": "...", "phone": "..."}
        """
        try:
            full_name = billing.get("first_name", "").strip()
            email = billing.get("email", "").strip()
            phone = billing.get("phone", "").strip()

            if not phone:
                raise CounterpartyError("Телефон не указан в заказе")

            normalized = normalize_phone(phone)
            log.info("Поиск контрагента", phone=normalized)

            # Поиск по телефону
            rows = self.ms_client.find_by_filter("counterparty", f"phone={normalized}")

            if rows:
                # Найден один или несколько
                if len(rows) > 1:
                    log.warning("Найдено несколько контрагентов по телефону, берём первого",
                                phone=normalized, count=len(rows))

                counterparty = rows[0]
                cp_meta = counterparty["meta"]
                cp_id = counterparty["id"]

                # Дозаполняем имя/почту из заказа (внешние системы, напр. amoCRM,
                # часто создают контрагента с именем-телефоном и без email).
                patch = self._build_enrichment_patch(counterparty, full_name, email)
                if counterparty.get("companyType") != "individual":
                    patch["companyType"] = "individual"
                if patch:
                    self.ms_client.put(f"entity/counterparty/{cp_id}", patch)
                    log.info("Контрагент дозаполнен данными из заказа",
                             id=cp_id, fields=list(patch.keys()))

                log.info("Контрагент найден", name=counterparty.get("name"), id=cp_id)
                return {"meta": cp_meta}

            # Не найден — создаём
            first_name, last_name, middle_name = split_full_name(full_name)

            new_cp_data = {
                "name": full_name or "Без имени",
                "companyType": "individual",
                "firstName": first_name,
                "phone": normalized,
            }

            if last_name:
                new_cp_data["lastName"] = last_name
            if middle_name:
                new_cp_data["middleName"] = middle_name
            if email:
                new_cp_data["email"] = email

            result = self.ms_client.post("entity/counterparty", new_cp_data)
            log.info("Контрагент создан", name=full_name, id=result["id"])
            return {"meta": result["meta"]}

        except CounterpartyError:
            raise
        except Exception as e:
            log.critical("Ошибка при работе с контрагентом", error=str(e))
            raise CounterpartyError(f"Не удалось найти/создать контрагента: {e}") from e
