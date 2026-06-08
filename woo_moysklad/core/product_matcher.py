# Сопоставление товаров и услуг доставки WC → МС

import re

from woo_moysklad.logger import get_logger

log = get_logger(__name__)


def _to_volume(value) -> float:
    """Объём товара МС → float (м³). None/мусор → 0.0."""
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


class ProductMatcher:
    """Сопоставление товаров WC → МС по артикулу (SKU) и управление услугами доставки."""

    def __init__(self, ms_client):
        self.ms_client = ms_client
        self._product_cache: dict[str, dict] = {}  # sku → {"meta": ..., "volume": float}
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

    def find_product(self, sku: str, product_name: str = "") -> tuple[dict | None, bool, float]:
        """Найти товар в МС по артикулу (article), fallback на externalCode.

        Возвращает (meta-ссылку или None, is_opened, volume_m3).
        is_opened=True для товаров "из видеообзора" → склад "Вскрытые".
        volume_m3 — нативное поле `volume` товара МС (для подбора упаковки), 0.0 если не задан.
        """
        if not sku:
            log.error("Пустой артикул товара")
            return None, False, 0.0

        clean_sku, is_opened = self._parse_video_review_sku(sku, product_name)

        if is_opened:
            log.info("Товар из видеообзора, SKU очищен", original=sku, clean=clean_sku)

        # Проверяем кэш
        if clean_sku in self._product_cache:
            cached = self._product_cache[clean_sku]
            return cached["meta"], is_opened, cached["volume"]

        # Поиск по article, fallback на externalCode
        for field, value in (("article", clean_sku), ("externalCode", clean_sku)):
            rows = self.ms_client.find_by_filter("product", f"{field}={value}")
            if rows:
                meta = {"meta": rows[0]["meta"]}
                volume = _to_volume(rows[0].get("volume"))
                self._product_cache[clean_sku] = {"meta": meta, "volume": volume}
                log.info("Товар найден", sku=clean_sku, by=field, id=rows[0]["id"], volume=volume)
                return meta, is_opened, volume

        log.error("Товар не найден в МС", sku=clean_sku, original_sku=sku)
        return None, False, 0.0

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

    def build_positions_from_normalized(self, line_items, delivery_services) -> dict:
        """Собрать позиции заказа из нормализованных данных.

        Возвращает {"regular": [...], "opened": [], "services": [...],
        "item_volumes": [(volume_m3, quantity), ...]}.
        item_volumes — объёмы найденных товаров (для подбора упаковки).
        Цена и количество уже готовы в NormalizedLineItem/NormalizedDeliveryService.
        """
        regular = []
        opened = []
        services = []
        item_volumes = []

        for item in line_items:
            product_meta, is_opened, volume = self.find_product(item.sku, item.title)
            if product_meta is None:
                continue

            position = {
                "quantity": item.quantity,
                "price": item.price_cents,
                "discount": 0,
                "vat": 0,
                "assortment": product_meta,
            }
            item_volumes.append((volume, item.quantity))

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

        return {"regular": regular, "opened": opened, "services": services,
                "item_volumes": item_volumes}
