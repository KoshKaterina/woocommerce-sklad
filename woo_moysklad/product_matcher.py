# Сопоставление товаров и услуг доставки WC → МС

import re

from .logger import get_logger

log = get_logger(__name__)


class ProductMatcher:
    """Сопоставление товаров WC → МС по артикулу (SKU) и управление услугами доставки."""

    def __init__(self, ms_client):
        self.ms_client = ms_client
        self._product_cache: dict[str, dict] = {}  # sku → meta
        self._service_cache: dict[str, dict] = {}   # name → meta

    @staticmethod
    def _parse_video_review_sku(sku: str, product_name: str) -> tuple[str, bool]:
        """Определить реальный SKU и признак 'из видеообзора'.

        Если в названии товара есть 'из видеообзор', убираем суффикс -N из SKU.
        Возвращает (clean_sku, is_opened).
        """
        if "из видеообзор" in product_name.lower():
            clean = re.sub(r'-\d+$', '', sku)
            return clean, True
        return sku, False

    def find_product(self, sku: str, product_name: str = "") -> tuple[dict | None, bool]:
        """Найти товар в МС по артикулу (article), fallback на externalCode.

        Возвращает (meta-ссылку или None, is_opened).
        is_opened=True для товаров "из видеообзора" → склад "Вскрытые".
        """
        if not sku:
            log.error("Пустой артикул товара")
            return None, False

        clean_sku, is_opened = self._parse_video_review_sku(sku, product_name)

        if is_opened:
            log.info("Товар из видеообзора, SKU очищен", original=sku, clean=clean_sku)

        # Проверяем кэш
        if clean_sku in self._product_cache:
            return self._product_cache[clean_sku], is_opened

        # Поиск по article
        rows = self.ms_client.find_by_filter("product", f"article={clean_sku}")
        if rows:
            meta = {"meta": rows[0]["meta"]}
            self._product_cache[clean_sku] = meta
            log.info("Товар найден по article", sku=clean_sku, id=rows[0]["id"])
            return meta, is_opened

        # Fallback: поиск по externalCode
        rows = self.ms_client.find_by_filter("product", f"externalCode={clean_sku}")
        if rows:
            meta = {"meta": rows[0]["meta"]}
            self._product_cache[clean_sku] = meta
            log.info("Товар найден по externalCode", sku=clean_sku, id=rows[0]["id"])
            return meta, is_opened

        log.error("Товар не найден в МС", sku=clean_sku, original_sku=sku)
        return None, False

    def find_or_create_service(self, name: str) -> dict | None:
        """Найти или создать услугу в МС по имени.

        Возвращает meta-ссылку или None.
        """
        if not name:
            return None

        # Проверяем кэш
        if name in self._service_cache:
            return self._service_cache[name]

        try:
            # Поиск по имени
            rows = self.ms_client.find_by_filter("service", f"name={name}")
            if rows:
                meta = {"meta": rows[0]["meta"]}
                self._service_cache[name] = meta
                log.info("Услуга найдена", name=name)
                return meta

            # Не найдена — создаём
            log.warning("Услуга не найдена, создаём новую", name=name)
            result = self.ms_client.post("entity/service", {"name": name})
            meta = {"meta": result["meta"]}
            self._service_cache[name] = meta
            log.info("Услуга создана", name=name, id=result["id"])
            return meta

        except Exception as e:
            log.error("Ошибка при работе с услугой", name=name, error=str(e))
            return None

    def build_positions(self, line_items: list, shipping_lines: list,
                        is_card_payment: bool) -> dict[str, list[dict]]:
        """Собрать позиции заказа, разделённые по категориям.

        Возвращает {"regular": [...], "opened": [...], "services": [...]}.
        regular — обычные товары, opened — товары из видеообзора, services — услуги.
        """
        regular = []
        opened = []
        services = []

        # Товары
        for item in line_items:
            sku = item.get("sku", "")
            product_name = item.get("name", "")
            product_meta, is_opened = self.find_product(sku, product_name)
            if product_meta is None:
                continue

            price = int(float(item.get("price", 0)) * 100)  # Копейки
            quantity = int(item.get("quantity", 1))

            position = {
                "quantity": quantity,
                "price": price,
                "discount": 0,
                "vat": 0,
                "assortment": product_meta,
            }

            if is_opened:
                opened.append(position)
            else:
                regular.append(position)

        # Услуги доставки
        for sl in shipping_lines:
            method_title = sl.get("method_title", "")
            service_meta = self.find_or_create_service(method_title)
            if service_meta is None:
                continue

            if is_card_payment:
                price = 0
            else:
                price = int(float(sl.get("total", 0)) * 100)  # Копейки

            services.append({
                "quantity": 1,
                "price": price,
                "discount": 0,
                "vat": 0,
                "assortment": service_meta,
            })

        return {"regular": regular, "opened": opened, "services": services}

    def build_positions_from_normalized(self, line_items, delivery_services) -> dict[str, list[dict]]:
        """Собрать позиции заказа из нормализованных данных.

        Возвращает {"regular": [...], "opened": [], "services": [...]}.
        Цена и количество уже готовы в NormalizedLineItem/NormalizedDeliveryService.
        """
        regular = []
        opened = []
        services = []

        for item in line_items:
            product_meta, is_opened = self.find_product(item.sku, item.title)
            if product_meta is None:
                continue

            position = {
                "quantity": item.quantity,
                "price": item.price_cents,
                "discount": 0,
                "vat": 0,
                "assortment": product_meta,
            }

            if is_opened:
                opened.append(position)
            else:
                regular.append(position)

        for svc in delivery_services:
            service_meta = self.find_or_create_service(svc.name)
            if service_meta is None:
                continue

            services.append({
                "quantity": 1,
                "price": svc.price_cents,
                "discount": 0,
                "vat": 0,
                "assortment": service_meta,
            })

        return {"regular": regular, "opened": opened, "services": services}
