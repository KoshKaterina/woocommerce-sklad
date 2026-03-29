# Обработчик контрагентов: поиск/создание в Мой Склад

import re

from .exceptions import CounterpartyError
from .logger import get_logger

log = get_logger(__name__)


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

                # Если companyType != "individual" → обновить
                if counterparty.get("companyType") != "individual":
                    log.info("Обновляем companyType на individual", name=counterparty.get("name"))
                    cp_id = counterparty["id"]
                    self.ms_client.put(f"entity/counterparty/{cp_id}", {"companyType": "individual"})

                log.info("Контрагент найден", name=counterparty.get("name"), id=counterparty["id"])
                return {"meta": cp_meta}

            # Не найден — создаём
            first_name, last_name, middle_name = split_full_name(full_name)

            new_cp_data = {
                "name": full_name or "Без имени",
                "companyType": "individual",
                "firstName": first_name,
                "phones": [{"value": normalized}],
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
