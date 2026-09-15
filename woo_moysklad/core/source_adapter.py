# Адаптеры источников заказов для унифицированной сверки (WooCommerce, InSales).

from abc import ABC, abstractmethod
from datetime import datetime

from woo_moysklad.logger import get_logger
from woo_moysklad.core.field_mappers import is_manual_payment_mark

log = get_logger(__name__)


def _utc_naive_iso(moment: datetime) -> str:
    """UTC-время строкой без смещения зоны: «2026-08-07T13:06:00».

    WooCommerce принимает даты фильтра только без хвоста «+00:00» — со смещением
    и dates_are_gmt=true он молча отдаёт пустой список (проверено на живом API
    07.08.2026). Микросекунды тоже убираем: в фильтре они не нужны.
    """
    return moment.replace(tzinfo=None, microsecond=0).isoformat()


class SourceAdapter(ABC):
    """Абстрактный адаптер источника заказов для Reconciliation.

    Каждый источник реализует:
    - как получить список заказов, обновлённых в окне,
    - как построить номер заказа для поиска в МС,
    - как решить, нужно ли маркировать оплату,
    - как вызвать создание заказа и маркировку оплаты в OrderProcessor.
    """

    name: str

    @abstractmethod
    def fetch_modified_in_window(self, window_start: datetime,
                                 window_end: datetime) -> list[dict]:
        """Заказы, обновлённые в [window_start, window_end]."""

    @abstractmethod
    def order_id(self, raw_order: dict) -> str:
        """ID заказа в системе-источнике (для логов)."""

    @abstractmethod
    def ms_order_number(self, raw_order: dict) -> str:
        """Номер заказа в МС, по которому искать (с суффиксами, если есть)."""

    @abstractmethod
    def should_mark_paid(self, raw_order: dict) -> bool:
        """Нужно ли маркировать оплату для этого заказа."""

    @abstractmethod
    def process(self, raw_order: dict) -> list[dict]:
        """Создать заказ в МС (идемпотентно: пропускает, если уже есть)."""

    @abstractmethod
    def mark_paid(self, raw_order: dict) -> list[dict]:
        """Создать входящий платёж в МС."""


class WooSourceAdapter(SourceAdapter):
    """WooCommerce: фильтр по date_modified, mark_paid при processing и не-ручной оплате."""

    name = "woocommerce"

    def __init__(self, woo_client, order_processor):
        self.woo = woo_client
        self.processor = order_processor

    def fetch_modified_in_window(self, window_start: datetime,
                                 window_end: datetime) -> list[dict]:
        # Окно приходит в UTC (reconciliation.run), поэтому dates_are_gmt=True:
        # иначе WooCommerce прочитает даты как время сайта (МСК) и окно уедет на 3 часа.
        #
        # ⚠️ Дату шлём БЕЗ смещения зоны. isoformat() у aware-datetime даёт хвост
        # «+00:00», и WooCommerce в паре с dates_are_gmt=true на такую строку
        # отдаёт ПУСТОЙ список — сверка девять дней возвращала ноль заказов и
        # молча не ловила потери (07.08.2026, заказ #18287). Проверено на живом
        # API: со смещением 0 заказов, без смещения — находит.
        return self.woo.get_orders(
            modified_after=_utc_naive_iso(window_start),
            modified_before=_utc_naive_iso(window_end),
            dates_are_gmt=True,
        )

    def order_id(self, raw_order: dict) -> str:
        return str(raw_order["id"])

    def ms_order_number(self, raw_order: dict) -> str:
        return str(raw_order["id"])

    def should_mark_paid(self, raw_order: dict) -> bool:
        if raw_order.get("status") not in ("processing", "completed"):
            return False
        # "При получении" (COD), ручная предоплата ("На карту", "Банковский
        # перевод") и "Другой способ" маркируются вручную менеджером — автоматом не помечаем
        return not is_manual_payment_mark(raw_order.get("payment_method_title", ""))

    def process(self, raw_order: dict) -> list[dict]:
        return self.processor.process_order(raw_order)

    def mark_paid(self, raw_order: dict) -> list[dict]:
        return self.processor.mark_paid(raw_order)


class InSalesSourceAdapter(SourceAdapter):
    """InSales: фильтр по updated_since, mark_paid при paid_at + financial_status=paid."""

    name = "insales"

    def __init__(self, insales_client, order_processor):
        self.insales = insales_client
        self.processor = order_processor

    def fetch_modified_in_window(self, window_start: datetime,
                                 window_end: datetime) -> list[dict]:
        # InSales API умеет только updated_since (без верхней границы)
        # — фильтруем по updated_at на клиенте
        updated_since = window_start.isoformat()
        orders = self.insales.get_orders(updated_since=updated_since)
        return [o for o in orders if self._updated_within(o, window_end)]

    @staticmethod
    def _updated_within(raw_order: dict, window_end: datetime) -> bool:
        # updated_at приходит в зоне магазина (+03:00), window_end — UTC:
        # сравнивать можно только как datetime, ISO-строки с разными зонами
        # лексикографически несравнимы
        raw = raw_order.get("updated_at") or ""
        try:
            return datetime.fromisoformat(raw) <= window_end
        except (ValueError, TypeError):
            # не распарсилось — не теряем заказ, создание идемпотентно
            return True

    def order_id(self, raw_order: dict) -> str:
        return str(raw_order.get("id", ""))

    def ms_order_number(self, raw_order: dict) -> str:
        from woo_moysklad.insales.normalizer import _INSALES_ORDER_SUFFIX
        number = raw_order.get("number") or raw_order.get("id", "")
        return f"{number}{_INSALES_ORDER_SUFFIX}"

    def should_mark_paid(self, raw_order: dict) -> bool:
        paid_at = raw_order.get("paid_at")
        financial_status = raw_order.get("financial_status", "")
        return paid_at is not None and financial_status == "paid"

    def process(self, raw_order: dict) -> list[dict]:
        return self.processor.process_insales_order(raw_order)

    def mark_paid(self, raw_order: dict) -> list[dict]:
        return self.processor.mark_paid_insales(raw_order)
