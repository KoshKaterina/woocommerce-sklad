# Промежуточный формат заказа: платформо-независимый

from dataclasses import dataclass, field

from woo_moysklad.core.address_parser import ShipmentAddressParts


@dataclass
class NormalizedCustomer:
    """Данные клиента для создания контрагента в МС."""
    full_name: str          # "Ирина Фролова" — готово для counterparty
    phone: str              # Сырой телефон, нормализуется в CounterpartyHandler
    email: str = ""


@dataclass
class NormalizedLineItem:
    """Одна товарная позиция."""
    sku: str
    title: str
    price_cents: int        # Цена в копейках (финальная, после скидок)
    quantity: int


@dataclass
class NormalizedDeliveryService:
    """Услуга доставки (позиция-услуга в заказе)."""
    name: str               # Имя услуги в МС ("CDEK: Самовывоз (4 дней)")
    price_cents: int        # Копейки (0 для предоплаты, реальная для COD)


@dataclass
class NormalizedOrder:
    """Платформо-независимый формат заказа для обработки в МС."""

    source: str             # "woocommerce" / "insales"
    order_id: str           # ID заказа на платформе (для дедупликации)
    order_number: str       # Отображаемый номер ("17620 Tangemshop" / "15674")

    customer: NormalizedCustomer

    line_items: list[NormalizedLineItem] = field(default_factory=list)
    delivery_services: list[NormalizedDeliveryService] = field(default_factory=list)

    # Замапленные атрибуты (None = не определён / пропустить)
    payment_title: str = ""
    payment_type_key: str | None = None      # "prepaid" / "noncash" / None
    delivery_sd_key: str | None = None       # "cdek" / "yandex" / None
    delivery_type_key: str | None = None     # "pvz" / "postamat" / "courier" / None
    pvz_code: str | None = None
    shipment_address: str | None = None
    shipment_address_parts: "ShipmentAddressParts | None" = None  # для shipmentAddressFull
    description: str | None = None           # Комментарий покупателя
    promo_code: str | None = None

    # Переопределения конфига (None = использовать дефолт)
    organization_id: str | None = None
    state_id: str | None = None
    project_id: str | None = None
    sales_channel_id: str | None = None

    # Состояние оплаты
    is_paid: bool = False
    is_cod: bool = False                     # COD: не маркировать оплату

    # COD наценка → отдельная услуга в заказе
    cod_margin_amount_cents: int = 0

    # Стоимость доставки для атрибута (доставка + наценка)
    delivery_cost_attr_value: str | None = None

    # Предварительная стоимость товаров (для атрибута)
    estimated_cost: str | None = None

    # Сумма к оплате получателю (для COD)
    total_to_pay: str | None = None
