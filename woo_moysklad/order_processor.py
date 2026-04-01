# Основной обработчик заказа: сборка и отправка в Мой Склад

import time

from .counterparty_handler import CounterpartyHandler
from .exceptions import CounterpartyError, OrderProcessingError
from .field_mappers import (
    build_attribute,
    build_shipment_address,
    detect_delivery_type,
    extract_courier_comment,
    extract_pvz_code,
    is_card_payment,
    map_delivery_sd,
    map_delivery_type,
    map_payment_type,
)
from .logger import get_logger
from .product_matcher import ProductMatcher

log = get_logger(__name__)

# ВРЕМЕННО: суффикс для тестового режима (чтобы номера не пересекались с другой интеграцией)
# Убрать после завершения тестирования (поставить "")
_TEST_ORDER_SUFFIX = "_1"


class OrderProcessor:
    """Обработка заказа WC: маппинг полей, сборка позиций, создание/обновление в МС."""

    def __init__(self, config, ms_client, counterparty_handler: CounterpartyHandler,
                 product_matcher: ProductMatcher):
        self.config = config
        self.ms = ms_client
        self.cp_handler = counterparty_handler
        self.pm = product_matcher

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
        """Получить UUID элемента справочника 'Доставка (СД)' по ключу маппинга."""
        mapping = {
            "cdek": self.config.MS_DELIVERY_SD_CDEK_ID,
            "yandex": self.config.MS_DELIVERY_SD_YANDEX_ID,
            "pickup": self.config.MS_DELIVERY_SD_PICKUP_ID,
        }
        return mapping.get(key)

    def _resolve_delivery_type_id(self, key: str) -> str | None:
        """Получить UUID элемента справочника 'Вид доставки' по ключу маппинга."""
        mapping = {
            "pvz": self.config.MS_DELIVERY_TYPE_PVZ_ID,
            "postamat": self.config.MS_DELIVERY_TYPE_POSTAMAT_ID,
            "courier": self.config.MS_DELIVERY_TYPE_COURIER_ID,
        }
        return mapping.get(key)

    def _resolve_payment_type_id(self, key: str) -> str | None:
        """Получить UUID элемента справочника 'Прием платежа' по ключу маппинга."""
        mapping = {
            "prepaid": self.config.MS_PAYMENT_TYPE_PREPAID_ID,
            "noncash": self.config.MS_PAYMENT_TYPE_NONCASH_ID,
        }
        return mapping.get(key)

    def process_order(self, order_data: dict) -> list[dict]:
        """Создать заказ WC в МС (никогда не обновляет существующие).

        Может создать 1 или 2 заказа (при наличии товаров "из видеообзора" вместе с обычными).
        Если заказ уже существует — пропускает. Возвращает список ответов API МС.
        """
        order_id = str(order_data["id"])
        log.info("Обработка заказа", wc_order_id=order_id)

        # --- 1. Контрагент (обязательно) ---
        billing = order_data.get("billing", {})
        try:
            agent_meta = self.cp_handler.find_or_create(billing)
        except CounterpartyError as e:
            log.critical("Не удалось найти/создать контрагента", error=str(e))
            raise OrderProcessingError(f"Ошибка контрагента: {e}") from e

        # --- 2. Маппинг полей (общие для всех заказов) ---
        payment_title = order_data.get("payment_method_title", "")
        shipping_lines = order_data.get("shipping_lines", [])
        method_title = shipping_lines[0].get("method_title", "") if shipping_lines else ""

        mapping_ctx = {
            "shipment_address": self._safe_map("build_shipment_address",
                                               lambda: build_shipment_address(order_data)),
            "pvz_code": self._safe_map("extract_pvz_code",
                                       lambda: extract_pvz_code(order_data)),
            "payment_type_key": self._safe_map("map_payment_type",
                                               lambda: map_payment_type(payment_title)),
            "courier_comment": self._safe_map("extract_courier_comment",
                                              lambda: extract_courier_comment(order_data)),
            "payment_method_str": payment_title if payment_title else None,
            "delivery_sd_key": self._safe_map("map_delivery_sd",
                                              lambda: map_delivery_sd(method_title)),
            "delivery_type_key": self._safe_map("map_delivery_type",
                                                lambda: map_delivery_type(method_title)),
            "description": order_data.get("customer_note", "").strip(),
            "payment_title": payment_title,
        }

        # --- 3. Позиции (категоризированные) ---
        card_payment = is_card_payment(payment_title)
        pos = self.pm.build_positions(
            order_data.get("line_items", []),
            shipping_lines,
            card_payment,
        )

        regular = pos["regular"]
        opened = pos["opened"]
        services = pos["services"]

        has_regular = bool(regular)
        has_opened = bool(opened)

        if not has_regular and not has_opened:
            log.critical("Заказ без товаров", wc_order_id=order_id)
            raise OrderProcessingError(f"Заказ #{order_id} не содержит товаров")

        # --- 4. Определяем сценарий и создаём заказы ---
        order_specs = []

        sfx = _TEST_ORDER_SUFFIX  # ВРЕМЕННО: тестовый суффикс

        if has_regular and has_opened:
            # Смешанный: 2 заказа
            # Основной: обычные товары + все услуги, склад "Основной", реальный номер
            order_specs.append({
                "order_number": f"{order_id}{sfx}",
                "store_id": self.config.MS_STORE_ID,
                "positions": regular + services,
            })
            # Дополнительный: товары из видеообзора, склад "Вскрытые", номер + sfx + _1, без услуг
            order_specs.append({
                "order_number": f"{order_id}{sfx}_1",
                "store_id": self.config.MS_STORE_OPENED_ID or self.config.MS_STORE_ID,
                "positions": opened,
            })
        elif has_opened:
            # Только товары из видеообзора + услуги → 1 заказ, склад "Вскрытые"
            order_specs.append({
                "order_number": f"{order_id}{sfx}",
                "store_id": self.config.MS_STORE_OPENED_ID or self.config.MS_STORE_ID,
                "positions": opened + services,
            })
        else:
            # Только обычные товары (или пусто) + услуги → 1 заказ, склад "Основной"
            order_specs.append({
                "order_number": f"{order_id}{sfx}",
                "store_id": self.config.MS_STORE_ID,
                "positions": regular + services,
            })

        # --- 5. Создание каждого заказа ---
        results = []
        for spec in order_specs:
            result = self._create_single_order(
                spec=spec,
                agent_meta=agent_meta,
                mapping_ctx=mapping_ctx,
                wc_order_id=order_id,
            )
            if result:
                results.append(result)

        return results

    def _create_single_order(self, *, spec: dict, agent_meta: dict,
                             mapping_ctx: dict, wc_order_id: str) -> dict | None:
        """Создать один заказ в МС (никогда не обновляет существующие)."""
        order_number = spec["order_number"]
        store_id = spec["store_id"]
        positions = spec["positions"]

        # Проверка дубликата: если заказ уже есть — никогда не обновляем
        # (менеджеры могут вручную менять заказ в МС, обновление затрёт их правки)
        existing = self._find_existing_order(order_number)
        if existing:
            log.info("Заказ уже существует в МС, пропускаем",
                     order_number=order_number, ms_name=existing.get("name"))
            return existing

        # Расчёт стоимостей из позиций
        estimated_cost, delivery_cost, total_to_pay = self._calc_costs(
            positions, mapping_ctx["payment_title"])

        # Доп. поля
        attributes = self._build_attributes(
            order_number=order_number,
            estimated_cost=estimated_cost,
            delivery_cost=delivery_cost,
            total_to_pay=total_to_pay,
            **{k: mapping_ctx[k] for k in (
                "payment_method_str", "payment_type_key", "delivery_sd_key",
                "delivery_type_key", "pvz_code", "courier_comment",
            )},
        )

        # Тело запроса
        body = self._build_body(
            agent_meta=agent_meta,
            store_id=store_id,
            positions=positions,
            attributes=attributes,
            shipment_address=mapping_ctx["shipment_address"],
            description=mapping_ctx["description"],
        )

        # Отправка (только POST, никогда PUT)
        try:
            result = self.ms.post("entity/customerorder", body)
            log.info("Заказ создан в МС", order_number=order_number,
                     ms_name=result.get("name"))
            return result

        except Exception as e:
            log.critical("Ошибка создания заказа в МС",
                         order_number=order_number, error=str(e))
            raise OrderProcessingError(
                f"Ошибка МС при создании заказа #{order_number}: {e}") from e

    def _calc_costs(self, positions: list, payment_title: str) -> tuple[str, str, str]:
        """Расчёт стоимостей из позиций. Возвращает (estimated_cost, delivery_cost, total_to_pay)."""
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

        if "при получении" in payment_title.lower():
            total_to_pay = str(all_total // 100)
        else:
            total_to_pay = "0"

        return estimated_cost, delivery_cost, total_to_pay

    def _build_attributes(self, *, order_number, payment_method_str, payment_type_key,
                          delivery_sd_key, delivery_type_key, pvz_code,
                          delivery_cost, estimated_cost, total_to_pay,
                          courier_comment) -> list:
        """Собрать список доп. полей заказа."""
        attributes = []
        cfg = self.config

        self._add_attr(attributes, cfg.MS_ATTR_ORDER_NUMBER_ID, order_number)
        self._add_attr(attributes, cfg.MS_ATTR_PAYMENT_METHOD_ID, payment_method_str)

        # Приём платежа (customentity)
        if payment_type_key:
            pt_element_id = self._resolve_payment_type_id(payment_type_key)
            if pt_element_id and pt_element_id != "uuid-уточнить":
                attr = build_attribute(
                    cfg.MS_ATTR_PAYMENT_TYPE_ID, "custom",
                    is_custom_entity=True,
                    dictionary_id=cfg.MS_CUSTOMENTITY_PAYMENT_TYPE_ID,
                    element_id=pt_element_id,
                )
                if attr:
                    attributes.append(attr)

        # Доставка СД (customentity)
        if delivery_sd_key:
            sd_element_id = self._resolve_delivery_sd_id(delivery_sd_key)
            if sd_element_id and sd_element_id != "uuid-уточнить":
                attr = build_attribute(
                    cfg.MS_ATTR_DELIVERY_SD_ID, "custom",
                    is_custom_entity=True,
                    dictionary_id=cfg.MS_CUSTOMENTITY_DELIVERY_SD_ID,
                    element_id=sd_element_id,
                )
                if attr:
                    attributes.append(attr)

        # Вид доставки (customentity)
        if delivery_type_key:
            dt_element_id = self._resolve_delivery_type_id(delivery_type_key)
            if dt_element_id and dt_element_id != "uuid-уточнить":
                attr = build_attribute(
                    cfg.MS_ATTR_DELIVERY_TYPE_ID, "custom",
                    is_custom_entity=True,
                    dictionary_id=cfg.MS_CUSTOMENTITY_DELIVERY_TYPE_ID,
                    element_id=dt_element_id,
                )
                if attr:
                    attributes.append(attr)

        self._add_attr(attributes, cfg.MS_ATTR_PVZ_CODE_ID, pvz_code)
        self._add_attr(attributes, cfg.MS_ATTR_DELIVERY_COST_ID, delivery_cost)
        self._add_attr(attributes, cfg.MS_ATTR_ESTIMATED_COST_ID, estimated_cost)
        self._add_attr(attributes, cfg.MS_ATTR_TOTAL_TO_PAY_ID, total_to_pay)
        self._add_attr(attributes, cfg.MS_ATTR_COURIER_COMMENT_ID, courier_comment)

        return attributes

    def _build_body(self, *, agent_meta, store_id, positions, attributes,
                    shipment_address, description) -> dict:
        """Собрать тело запроса заказа покупателя."""
        cfg = self.config
        body = {
            "organization": self.ms.make_meta("organization", cfg.MS_ORGANIZATION_ID),
            "agent": agent_meta,
            "store": self.ms.make_meta("store", store_id),
            "rate": {"currency": self.ms.make_meta("currency", cfg.MS_CURRENCY_RUB_ID)},
        }

        if cfg.MS_SALES_CHANNEL_ID:
            body["salesChannel"] = self.ms.make_meta("saleschannel", cfg.MS_SALES_CHANNEL_ID)

        if cfg.MS_STATE_NEW_LEAD_ID:
            body["state"] = self.ms.make_meta("state", cfg.MS_STATE_NEW_LEAD_ID)

        if description:
            body["description"] = description
        if shipment_address:
            body["shipmentAddress"] = shipment_address
        if positions:
            body["positions"] = positions
        if attributes:
            body["attributes"] = attributes

        return body

    def _safe_map(self, name: str, func):
        """Выполнить маппинг с обработкой ошибок. WARNING при ошибке, возврат None."""
        try:
            return func()
        except Exception as e:
            log.warning("Ошибка маппинга поля", field=name, error=str(e))
            return None

    def mark_paid(self, order_data: dict) -> list[dict]:
        """Проставить оплату для заказа в МС (создать входящий платёж).

        Находит все заказы по номеру (основной + _1) и создаёт платёж на сумму каждого.
        Не трогает остальные поля заказа.
        """
        order_id = str(order_data["id"])
        log.info("Проставление оплаты", wc_order_id=order_id)

        results = []
        # ВРЕМЕННО: _TEST_ORDER_SUFFIX добавляется к номерам (убрать после тестирования)
        sfx = _TEST_ORDER_SUFFIX
        for suffix in (sfx, f"{sfx}_1"):
            order_number = f"{order_id}{suffix}"
            existing = self._find_existing_order(order_number)

            # Retry для основного заказа при гонке с order.created
            if not existing and suffix == sfx:
                for attempt in range(3):
                    time.sleep(2 * (attempt + 1))  # 2, 4, 6 сек
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

            # Проверка: платёж уже существует
            if existing.get("payedSum", 0) > 0:
                log.info("Платёж уже существует, пропускаем",
                         order_number=order_number, payed_sum=existing.get("payedSum"))
                continue

            # Создаём входящий платёж
            date_paid = order_data.get("date_paid")
            moment = date_paid.replace("T", " ") if date_paid else None

            payment_body = {
                "organization": self.ms.make_meta("organization", self.config.MS_ORGANIZATION_ID),
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
