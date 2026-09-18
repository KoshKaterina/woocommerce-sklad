# Основной обработчик заказа: сборка и отправка в Мой Склад

import time
from datetime import datetime

from woo_moysklad.core.counterparty_handler import CounterpartyHandler
from woo_moysklad.exceptions import CounterpartyError, OrderProcessingError
from woo_moysklad.core.field_mappers import build_attribute
from woo_moysklad.logger import get_logger
from woo_moysklad.core.normalized_order import NormalizedOrder
from woo_moysklad.core.order_locks import claim_order
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
        normalized = normalize_wc_order(
            order_data, preorder_enabled=bool(self.config.MS_ATTR_PREORDER_SUMMARY_ID))
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
        """Маркировка оплаты заказа WooCommerce (основной + _1 если был вскрытый).

        Заодно дозаполняет контрагента (email/имя) свежими данными заказа: на
        момент order.created email иногда ещё не был известен (или сам вебхук
        не дошёл — см. main.py), а order.updated при status=processing раньше
        шёл ТОЛЬКО в маркировку оплаты, минуя контрагента — email из заказа
        так и оставался ненаписанным в МС, даже если он был у клиента на сайте.
        """
        from woo_moysklad.woocommerce.normalizer import normalize_wc_order
        order_id = str(order_data["id"])
        date_paid = order_data.get("date_paid")
        self._enrich_counterparty(normalize_wc_order(order_data).customer, order_id=order_id)
        return self._mark_paid_internal(
            base_order_number=order_id,
            suffixes=["", "_1"],
            date_paid=date_paid,
            organization_id=self.config.MS_ORGANIZATION_ID,
        )

    def mark_paid_insales(self, order_data: dict) -> list[dict]:
        """Маркировка оплаты заказа InSales (один заказ, с суффиксом номера ' Tangemshop').

        Дозаполняет контрагента — см. docstring mark_paid, та же причина.
        """
        from woo_moysklad.insales.normalizer import _INSALES_ORDER_SUFFIX, normalize_insales_order
        number = str(order_data.get("number") or order_data.get("id", ""))
        paid_at = order_data.get("paid_at")
        organization_id = (
            self.config.MS_ORGANIZATION_INSALES_ID or self.config.MS_ORGANIZATION_ID
        )
        normalized = normalize_insales_order(order_data, self.config)
        self._enrich_counterparty(normalized.customer, order_id=number)
        return self._mark_paid_internal(
            base_order_number=f"{number}{_INSALES_ORDER_SUFFIX}",
            suffixes=[""],
            date_paid=paid_at,
            organization_id=organization_id,
        )

    def _enrich_counterparty(self, customer, *, order_id: str) -> None:
        """Дозаполнить контрагента (email/имя) по свежим данным заказа.

        Не блокирует маркировку оплаты при сбое — это дозаполнение, а не
        обязательный шаг создания заказа (тот уже прошёл раньше).
        """
        try:
            self.cp_handler.find_or_create_from_normalized(customer)
        except CounterpartyError as e:
            log.warning("mark_paid: не удалось дозаполнить контрагента",
                       order_id=order_id, error=str(e))

    # ──────────────────────────────────────────────
    # Универсальная обработка NormalizedOrder
    # ──────────────────────────────────────────────

    def process_normalized_order(self, order: NormalizedOrder) -> list[dict]:
        """Создать заказ в МС из нормализованных данных.

        Может создать 1 или 2 заказа (WC: при наличии товаров "из видеообзора").
        Никогда не обновляет существующие заказы.

        Номер заказа занимается замком на всю обработку: вебхук и сверка ходят
        сюда из разных потоков и без замка успевали создать два заказа на один
        номер сайта (см. core/order_locks.py). Кто пришёл вторым — выходит ни с
        чем, его работу уже делает первый.
        """
        with claim_order(order.order_number) as acquired:
            if not acquired:
                log.info("Заказ уже обрабатывается другим потоком, пропускаем",
                         source=order.source, order_id=order.order_id,
                         order_number=order.order_number)
                return []
            return self._process_claimed_order(order)

    def _process_claimed_order(self, order: NormalizedOrder) -> list[dict]:
        """Обработка заказа, номер которого уже занят замком."""
        log.info("Обработка заказа", source=order.source, order_id=order.order_id)

        preorder_enabled = bool(self.config.MS_ATTR_PREORDER_SUMMARY_ID) and order.source == "woocommerce"
        has_snapshot = preorder_enabled and any(item.preorder is not None for item in order.line_items)
        if has_snapshot and not all(type(item.preorder) is bool for item in order.line_items):
            raise OrderProcessingError("Неполный снимок предзаказа: заказ не передан в МС")
        if preorder_enabled and not has_snapshot:
            log.warning("Признак предзаказа неизвестен: Woo заказ без снимков, поле МС не заполняем",
                        order_id=order.order_id)

        # --- 1. Контрагент ---
        try:
            agent_meta = self.cp_handler.find_or_create_from_normalized(order.customer)
        except CounterpartyError as e:
            log.critical("Не удалось найти/создать контрагента", error=str(e))
            raise OrderProcessingError(f"Ошибка контрагента: {e}") from e

        # --- 2. Позиции ---
        pos = self.pm.build_positions_from_normalized(
            order.line_items, order.delivery_services)

        if has_snapshot and (len(pos.get("regular_lines", [])) +
                             len(pos.get("opened_lines", []))) != len(order.line_items):
            raise OrderProcessingError("Не все товары сопоставлены с МС: список предзаказа неполон")

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
                "source_lines": pos.get("regular_lines", []),
            })
            order_specs.append({
                "order_number": f"{order.order_number}_1",
                "store_id": cfg.MS_STORE_OPENED_ID or cfg.MS_STORE_ID,
                "positions": opened,
                "source_lines": pos.get("opened_lines", []),
            })
        elif has_opened:
            order_specs.append({
                "order_number": order.order_number,
                "store_id": cfg.MS_STORE_OPENED_ID or cfg.MS_STORE_ID,
                "positions": opened + services,
                "source_lines": pos.get("opened_lines", []),
            })
        else:
            order_specs.append({
                "order_number": order.order_number,
                "store_id": cfg.MS_STORE_ID,
                "positions": regular + services,
                "source_lines": pos.get("regular_lines", []),
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

        # Доп. поля
        attributes = self._build_attributes_from_normalized(
            order_number=order_number,
            order=order,
            source_lines=spec["source_lines"],
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

    def _build_attributes_from_normalized(self, *, order_number,
                                          order: NormalizedOrder, source_lines: list) -> list:
        """Собрать список доп. полей из NormalizedOrder."""
        attributes = []
        cfg = self.config

        self._add_attr(attributes, cfg.MS_ATTR_ORDER_NUMBER_ID, order_number)
        self._add_attr(attributes, cfg.MS_ATTR_PAYMENT_METHOD_ID,
                       order.payment_title if order.payment_title else None)

        # ⚠️ 06.08.2026: доп.поле «Доставка» (customentity со справочником служб)
        # удалено по решению Кати - вид доставки виден в услугах заказа, дублировать
        # его отдельным полем не нужно. delivery_sd_key остаётся: по нему выбирается
        # статус заказа для самовывоза из шоурума (см. _build_body_from_normalized).
        self._add_attr(attributes, cfg.MS_ATTR_PVZ_CODE_ID, order.pvz_code)
        # «Комментарий курьеру» (доп.поле) НЕ заполняем из заказа — менеджер пишет
        # его вручную при необходимости. Комментарий покупателя идёт только в
        # нативное поле «Комментарий» (body["description"]). Поля не связаны.
        self._add_attr(attributes, cfg.MS_ATTR_PROMO_CODE_ID, order.promo_code)
        # ClientID Яндекс.Метрики (string) — для сквозной аналитики; нет → не пишем
        self._add_attr(attributes, cfg.MS_ATTR_YM_CLIENT_ID, order.ym_client_id)
        # Ник Телеграма здесь НЕ пишем: он живёт у контрагента (CounterpartyHandler)

        # Снимок предзаказа полон - заполняем оба поля разом, одним условием на оба.
        # Разъехавшиеся условия дали бы заказ, где текст говорит «Предзаказ», а список
        # «Тип заявки» пуст или врёт, и в сделку amoCRM уехало бы второе.
        if (order.source == "woocommerce" and source_lines
                and all(type(item.preorder) is bool for item in source_lines)):
            if cfg.MS_ATTR_PREORDER_SUMMARY_ID:
                self._add_attr(attributes, cfg.MS_ATTR_PREORDER_SUMMARY_ID,
                               self._preorder_summary(source_lines))
            self._add_order_type_attr(
                attributes, is_preorder=any(item.preorder for item in source_lines))

        return attributes

    def _add_order_type_attr(self, attributes: list, *, is_preorder: bool) -> None:
        """Поле-справочник «Тип заявки»: его читает мост amgroup и кладёт в сделку amoCRM.

        Пустой идентификатор поля, справочника или нужного значения выключает передачу,
        старый поток при этом не меняется.
        """
        cfg = self.config
        element_id = (cfg.MS_DICT_ORDER_TYPE_PREORDER_ID if is_preorder
                      else cfg.MS_DICT_ORDER_TYPE_ORDER_ID)
        if not (cfg.MS_ATTR_ORDER_TYPE_ID and cfg.MS_DICT_ORDER_TYPE_ID and element_id):
            return
        attr = build_attribute(cfg.MS_ATTR_ORDER_TYPE_ID,
                               "Предзаказ" if is_preorder else "Заказ",
                               is_custom_entity=True,
                               dictionary_id=cfg.MS_DICT_ORDER_TYPE_ID,
                               element_id=element_id)
        if attr:
            attributes.append(attr)

    @staticmethod
    def _preorder_summary(lines: list) -> str:
        """Тип и точные ожидаемые строки заказа, без урезания длинного списка."""
        waiting = [item for item in lines if item.preorder]
        if not waiting:
            return "Тип: Заказ\nОжидаемые позиции: нет"

        def one_line(value):
            return " ".join(str(value).split())

        details = []
        for item in waiting:
            details.append(f"- {one_line(item.title)} [SKU: {one_line(item.sku)}] × {item.quantity}")
        return "Тип: Предзаказ\nОжидаемые позиции:\n" + "\n".join(details)

    def _build_body_from_normalized(self, *, agent_meta, store_id, positions,
                                    attributes, order: NormalizedOrder) -> dict:
        """Собрать тело запроса заказа покупателя."""
        cfg = self.config

        # Организация: переопределение из заказа или дефолт
        org_id = order.organization_id or cfg.MS_ORGANIZATION_ID
        state_id = order.state_id or cfg.MS_STATE_NEW_LEAD_ID
        # Самовывоз из офиса Sunscrypt (WC local_pickup) → отдельный статус «новый лид 1»
        if order.delivery_sd_key == "showroom" and cfg.MS_STATE_SHOWROOM_PICKUP_ID:
            state_id = cfg.MS_STATE_SHOWROOM_PICKUP_ID

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
