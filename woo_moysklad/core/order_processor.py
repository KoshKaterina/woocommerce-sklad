# Основной обработчик заказа: сборка и отправка в Мой Склад

import time
from datetime import datetime

from woo_moysklad.core.counterparty_handler import CounterpartyHandler
from woo_moysklad.exceptions import CounterpartyError, OrderProcessingError
from woo_moysklad.core.field_mappers import build_attribute
from woo_moysklad.logger import get_logger
from woo_moysklad.core.normalized_order import NormalizedOrder
from woo_moysklad.core.product_matcher import ProductMatcher

log = get_logger(__name__)


def _to_ms_moment(value):
    """ISO-дата(-время) → формат МС 'YYYY-MM-DD HH:MM:SS' (без таймзоны).

    WC отдаёт date_paid без таймзоны, InSales paid_at — с offset (+03:00),
    который МС не принимает. None если пусто/невалидно.
    """
    if not value:
        return None
    s = str(value).strip()
    try:
        return datetime.fromisoformat(s).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        # запасной разбор: убрать 'T' и отбросить таймзону/дробную часть
        return s.replace("T", " ")[:19]


def _to_number(value):
    """Привести значение стоимости к числу (для double-полей МС). None если пусто/невалидно."""
    if value is None or value == "":
        return None
    try:
        f = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return int(f) if f.is_integer() else f


class OrderProcessor:
    """Обработка заказов из WC и InSales: маппинг, сборка позиций, создание в МС."""

    def __init__(self, config, ms_client, counterparty_handler: CounterpartyHandler,
                 product_matcher: ProductMatcher):
        self.config = config
        self.ms = ms_client
        self.cp_handler = counterparty_handler
        self.pm = product_matcher

    # ──────────────────────────────────────────────
    # Точки входа по источнику
    # ──────────────────────────────────────────────

    def process_order(self, order_data: dict) -> list[dict]:
        """Точка входа WooCommerce: нормализация → обработка.

        Обратная совместимость: reconciliation.py и main.py вызывают этот метод.
        """
        from woo_moysklad.woocommerce.normalizer import normalize_wc_order
        normalized = normalize_wc_order(order_data)
        return self.process_normalized_order(normalized)

    def process_insales_order(self, order_data: dict) -> list[dict]:
        """Точка входа InSales: нормализация → обработка."""
        from woo_moysklad.insales.normalizer import normalize_insales_order
        normalized = normalize_insales_order(order_data, self.config)
        return self.process_normalized_order(normalized)

    def process_ucoz_order(self, raw_data: dict) -> list[dict]:
        """Точка входа uCoz: нормализация → обработка."""
        from woo_moysklad.ucoz.normalizer import normalize_ucoz_order
        normalized = normalize_ucoz_order(raw_data, self.config)
        return self.process_normalized_order(normalized)

    def mark_paid(self, order_data: dict) -> list[dict]:
        """Маркировка оплаты заказа WooCommerce (основной + _1 если был вскрытый)."""
        order_id = str(order_data["id"])
        date_paid = order_data.get("date_paid")
        return self._mark_paid_internal(
            base_order_number=order_id,
            suffixes=["", "_1"],
            date_paid=date_paid,
            organization_id=self.config.MS_ORGANIZATION_ID,
        )

    def mark_paid_insales(self, order_data: dict) -> list[dict]:
        """Маркировка оплаты заказа InSales (один заказ, с суффиксом номера ' Tangemshop')."""
        from woo_moysklad.insales.normalizer import _INSALES_ORDER_SUFFIX
        number = str(order_data.get("number") or order_data.get("id", ""))
        paid_at = order_data.get("paid_at")
        organization_id = (
            self.config.MS_ORGANIZATION_INSALES_ID or self.config.MS_ORGANIZATION_ID
        )
        return self._mark_paid_internal(
            base_order_number=f"{number}{_INSALES_ORDER_SUFFIX}",
            suffixes=[""],
            date_paid=paid_at,
            organization_id=organization_id,
        )

    # ──────────────────────────────────────────────
    # Универсальная обработка NormalizedOrder
    # ──────────────────────────────────────────────

    def process_normalized_order(self, order: NormalizedOrder) -> list[dict]:
        """Создать заказ в МС из нормализованных данных.

        Может создать 1 или 2 заказа (WC: при наличии товаров "из видеообзора").
        Никогда не обновляет существующие заказы.
        """
        log.info("Обработка заказа", source=order.source, order_id=order.order_id)

        # --- 1. Контрагент ---
        try:
            agent_meta = self.cp_handler.find_or_create_from_normalized(order.customer)
        except CounterpartyError as e:
            log.critical("Не удалось найти/создать контрагента", error=str(e))
            raise OrderProcessingError(f"Ошибка контрагента: {e}") from e

        # --- 2. Позиции ---
        pos = self.pm.build_positions_from_normalized(
            order.line_items, order.delivery_services)

        regular = pos["regular"]
        opened = pos["opened"]
        services = pos["services"]

        # COD наценка → отдельная услуга
        if order.cod_margin_amount_cents > 0:
            margin_meta = self.pm.find_or_create_service("Наценка за наложенный платеж")
            if margin_meta:
                services.append({
                    "quantity": 1,
                    "price": order.cod_margin_amount_cents,
                    "discount": 0,
                    "vat": 0,
                    "assortment": margin_meta,
                })

        has_regular = bool(regular)
        has_opened = bool(opened)

        if not has_regular and not has_opened:
            log.critical("Заказ без товаров", order_id=order.order_id)
            raise OrderProcessingError(f"Заказ #{order.order_id} не содержит товаров")

        # --- 3. Определяем сценарий ---
        order_specs = []
        cfg = self.config

        if has_regular and has_opened:
            # Смешанный: 2 заказа (только WC, InSales не имеет opened)
            order_specs.append({
                "order_number": order.order_number,
                "store_id": cfg.MS_STORE_ID,
                "positions": regular + services,
            })
            order_specs.append({
                "order_number": f"{order.order_number}_1",
                "store_id": cfg.MS_STORE_OPENED_ID or cfg.MS_STORE_ID,
                "positions": opened,
            })
        elif has_opened:
            order_specs.append({
                "order_number": order.order_number,
                "store_id": cfg.MS_STORE_OPENED_ID or cfg.MS_STORE_ID,
                "positions": opened + services,
            })
        else:
            order_specs.append({
                "order_number": order.order_number,
                "store_id": cfg.MS_STORE_ID,
                "positions": regular + services,
            })

        # --- 4. Создание каждого заказа ---
        results = []
        for spec in order_specs:
            result = self._create_single_order(
                spec=spec,
                agent_meta=agent_meta,
                order=order,
            )
            if result:
                results.append(result)

        return results

    # ──────────────────────────────────────────────
    # Внутренние методы
    # ──────────────────────────────────────────────

    def _find_existing_order(self, order_number: str) -> dict | None:
        """Поиск существующего заказа в МС по доп. полю 'Номер заказа на сайте'."""
        if not self.config.MS_ATTR_ORDER_NUMBER_ID:
            return None

        filter_str = (
            f"https://api.moysklad.ru/api/remap/1.2/entity/customerorder/metadata/"
            f"attributes/{self.config.MS_ATTR_ORDER_NUMBER_ID}={order_number}"
        )
        rows = self.ms.find_by_filter("customerorder", filter_str)
        return rows[0] if rows else None

    def _resolve_delivery_sd_id(self, key: str) -> str | None:
        mapping = {
            "cdek": self.config.MS_DELIVERY_SD_CDEK_ID,
            "dostavista": self.config.MS_DELIVERY_SD_DOSTAVISTA_ID,
            "showroom": self.config.MS_DELIVERY_SD_SHOWROOM_ID,
            "rms_pickup": self.config.MS_DELIVERY_SD_RMS_PICKUP_ID,
        }
        return mapping.get(key)

    def _resolve_delivery_type_num(self, key: str) -> int | None:
        """Вид доставки → числовой код нового поля (long).

        Справочник МС: 0 склад / 1 ПВЗ / 2 курьер / 3 почтомат / 4 почта / 5 экспорт.
        Интеграция выставляет только pvz / courier / postamat.
        """
        return {"pvz": 1, "courier": 2, "postamat": 3}.get(key)

    def _resolve_payment_type_id(self, key: str) -> str | None:
        mapping = {
            "prepaid": self.config.MS_PAYMENT_TYPE_PREPAID_ID,
            "noncash": self.config.MS_PAYMENT_TYPE_NONCASH_ID,
        }
        return mapping.get(key)

    def _create_single_order(self, *, spec: dict, agent_meta: dict,
                             order: NormalizedOrder) -> dict | None:
        """Создать один заказ в МС (никогда не обновляет существующие)."""
        order_number = spec["order_number"]
        store_id = spec["store_id"]
        positions = spec["positions"]

        existing = self._find_existing_order(order_number)
        if existing:
            log.info("Заказ уже существует в МС, пропускаем",
                     order_number=order_number, ms_name=existing.get("name"))
            return existing

        # Расчёт стоимостей
        estimated_cost, delivery_cost, total_to_pay = self._calc_costs_from_normalized(
            positions, order)

        # Доп. поля
        attributes = self._build_attributes_from_normalized(
            order_number=order_number,
            estimated_cost=estimated_cost,
            delivery_cost=delivery_cost,
            total_to_pay=total_to_pay,
            order=order,
        )

        # Тело запроса
        body = self._build_body_from_normalized(
            agent_meta=agent_meta,
            store_id=store_id,
            positions=positions,
            attributes=attributes,
            order=order,
        )

        try:
            result = self.ms.post("entity/customerorder", body)
            log.info("Заказ создан в МС", order_number=order_number,
                     ms_name=result.get("name"), source=order.source)
            return result
        except Exception as e:
            log.critical("Ошибка создания заказа в МС",
                         order_number=order_number, error=str(e))
            raise OrderProcessingError(
                f"Ошибка МС при создании заказа #{order_number}: {e}") from e

    def _calc_costs_from_normalized(self, positions: list,
                                    order: NormalizedOrder) -> tuple[str, str, str]:
        """Расчёт стоимостей из позиций и NormalizedOrder."""
        # Если нормализатор уже вычислил значения — используем их
        if order.estimated_cost and order.delivery_cost_attr_value:
            estimated_cost = order.estimated_cost
            delivery_cost = order.delivery_cost_attr_value
            total_to_pay = order.total_to_pay or "0"
            return estimated_cost, delivery_cost, total_to_pay

        # Fallback: расчёт из позиций (как в WC)
        products_total = sum(
            p["price"] * p["quantity"] for p in positions
            if p["assortment"].get("meta", {}).get("type") == "product"
        )
        services_total = sum(
            p["price"] * p["quantity"] for p in positions
            if p["assortment"].get("meta", {}).get("type") == "service"
        )
        all_total = products_total + services_total

        estimated_cost = str(products_total // 100)
        delivery_cost = str(services_total // 100)
        total_to_pay = str(all_total // 100) if order.is_cod else "0"

        return estimated_cost, delivery_cost, total_to_pay

    def _build_attributes_from_normalized(self, *, order_number, estimated_cost,
                                          delivery_cost, total_to_pay,
                                          order: NormalizedOrder) -> list:
        """Собрать список доп. полей из NormalizedOrder."""
        attributes = []
        cfg = self.config

        self._add_attr(attributes, cfg.MS_ATTR_ORDER_NUMBER_ID, order_number)
        self._add_attr(attributes, cfg.MS_ATTR_PAYMENT_METHOD_ID,
                       order.payment_title if order.payment_title else None)

        # Приём платежа (customentity)
        if order.payment_type_key:
            pt_element_id = self._resolve_payment_type_id(order.payment_type_key)
            if pt_element_id:
                attr = build_attribute(
                    cfg.MS_ATTR_PAYMENT_TYPE_ID, "custom",
                    is_custom_entity=True,
                    dictionary_id=cfg.MS_CUSTOMENTITY_PAYMENT_TYPE_ID,
                    element_id=pt_element_id,
                )
                if attr:
                    attributes.append(attr)

        # Доставка СД (customentity)
        if order.delivery_sd_key:
            sd_element_id = self._resolve_delivery_sd_id(order.delivery_sd_key)
            if sd_element_id:
                attr = build_attribute(
                    cfg.MS_ATTR_DELIVERY_SD_ID, "custom",
                    is_custom_entity=True,
                    dictionary_id=cfg.MS_CUSTOMENTITY_DELIVERY_SD_ID,
                    element_id=sd_element_id,
                )
                if attr:
                    attributes.append(attr)

        # Вид доставки (long: 1=ПВЗ, 2=курьер, 3=почтомат)
        if order.delivery_type_key:
            dt_num = self._resolve_delivery_type_num(order.delivery_type_key)
            if dt_num is not None:
                self._add_attr(attributes, cfg.MS_ATTR_DELIVERY_TYPE_ID, dt_num)

        self._add_attr(attributes, cfg.MS_ATTR_PVZ_CODE_ID, order.pvz_code)
        # Стоимости — поля double, передаём числом
        self._add_attr(attributes, cfg.MS_ATTR_DELIVERY_COST_ID, _to_number(delivery_cost))
        self._add_attr(attributes, cfg.MS_ATTR_ESTIMATED_COST_ID, _to_number(estimated_cost))
        self._add_attr(attributes, cfg.MS_ATTR_TOTAL_TO_PAY_ID, _to_number(total_to_pay))
        # «Комментарий курьеру» (доп.поле) НЕ заполняем из заказа — менеджер пишет
        # его вручную при необходимости. Комментарий покупателя идёт только в
        # нативное поле «Комментарий» (body["description"]). Поля не связаны.
        self._add_attr(attributes, cfg.MS_ATTR_PROMO_CODE_ID, order.promo_code)

        return attributes

    def _build_body_from_normalized(self, *, agent_meta, store_id, positions,
                                    attributes, order: NormalizedOrder) -> dict:
        """Собрать тело запроса заказа покупателя."""
        cfg = self.config

        # Организация: переопределение из заказа или дефолт
        org_id = order.organization_id or cfg.MS_ORGANIZATION_ID
        state_id = order.state_id or cfg.MS_STATE_NEW_LEAD_ID

        body = {
            "organization": self.ms.make_meta("organization", org_id),
            "agent": agent_meta,
            "store": self.ms.make_meta("store", store_id),
            "rate": {"currency": self.ms.make_meta("currency", cfg.MS_CURRENCY_RUB_ID)},
        }

        sales_channel_id = order.sales_channel_id or cfg.MS_SALES_CHANNEL_ID
        if sales_channel_id:
            body["salesChannel"] = self.ms.make_meta("saleschannel", sales_channel_id)

        if state_id:
            body["state"] = self.ms.make_state_meta("customerorder", state_id)

        if order.project_id:
            body["project"] = self.ms.make_meta("project", order.project_id)

        if order.description:
            body["description"] = order.description
        if order.shipment_address:
            body["shipmentAddress"] = order.shipment_address
        address_full = self._build_shipment_address_full(order)
        if address_full:
            body["shipmentAddressFull"] = address_full
        if positions:
            body["positions"] = positions
        if attributes:
            body["attributes"] = attributes

        return body

    def _build_shipment_address_full(self, order: NormalizedOrder) -> dict | None:
        """Собрать нативный объект shipmentAddressFull из разобранных компонентов.

        Строковые поля пишем напрямую; country — ссылка на справочник стран МС
        (резолв по названию, не хардкод). region в MVP не пишем (справочник) —
        он сохранён в addInfo. Пустые поля опускаем.
        """
        parts = order.shipment_address_parts
        if parts is None or parts.is_empty():
            return None

        full: dict = {}
        if parts.postal_code:
            full["postalCode"] = parts.postal_code
        if parts.city:
            full["city"] = parts.city
        if parts.street:
            full["street"] = parts.street
        if parts.house:
            full["house"] = parts.house
        if parts.apartment:
            full["apartment"] = parts.apartment
        if parts.add_info:
            full["addInfo"] = parts.add_info
        if parts.country_name:
            country_meta = self.ms.find_country_meta(parts.country_name)
            if country_meta:
                full["country"] = country_meta

        return full or None

    def _mark_paid_internal(self, *, base_order_number: str, suffixes: list[str],
                            date_paid: str | None,
                            organization_id: str) -> list[dict]:
        """Создать платежи в МС для заказов с именами `base_order_number{suffix}`.

        Для первого suffix'а делает retry (гонка с созданием заказа из вебхука).
        """
        log.info("Проставление оплаты", base_order_number=base_order_number)

        results = []
        first_suffix = suffixes[0] if suffixes else ""
        for suffix in suffixes:
            order_number = f"{base_order_number}{suffix}"
            existing = self._find_existing_order(order_number)

            if not existing and suffix == first_suffix:
                for attempt in range(3):
                    time.sleep(2 * (attempt + 1))
                    existing = self._find_existing_order(order_number)
                    if existing:
                        log.info("Заказ найден после retry",
                                 order_number=order_number, attempt=attempt + 1)
                        break
                if not existing:
                    log.warning("Заказ не найден в МС для оплаты после retry",
                                order_number=order_number)
                    continue
            elif not existing:
                continue

            if existing.get("payedSum", 0) > 0:
                log.info("Платёж уже существует, пропускаем",
                         order_number=order_number, payed_sum=existing.get("payedSum"))
                continue

            moment = _to_ms_moment(date_paid)

            payment_body = {
                "organization": self.ms.make_meta("organization", organization_id),
                "agent": {"meta": existing["agent"]["meta"]},
                "sum": existing["sum"],
                "applicable": True,
                "operations": [
                    {"meta": existing["meta"], "linkedSum": existing["sum"]}
                ],
            }
            if moment:
                payment_body["moment"] = moment

            try:
                payment = self.ms.post("entity/paymentin", payment_body)
                log.info("Платёж создан", order_number=order_number,
                         ms_name=existing.get("name"), payment_id=payment.get("id"))
                results.append(existing)
            except Exception as e:
                log.error("Ошибка создания платежа", order_number=order_number, error=str(e))
                raise OrderProcessingError(
                    f"Ошибка создания платежа для заказа #{order_number}: {e}") from e

        return results

    def _add_attr(self, attributes: list, attr_uuid: str, value):
        """Добавить доп. поле в список, если значение не None."""
        attr = build_attribute(attr_uuid, value)
        if attr:
            attributes.append(attr)
