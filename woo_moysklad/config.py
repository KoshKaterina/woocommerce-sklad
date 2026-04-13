# Конфигурация интеграции: чтение .env, валидация обязательных полей

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

from .logger import get_logger, setup_logging

log = get_logger(__name__)


@dataclass
class Config:
    """Конфигурация интеграции WooCommerce → Мой Склад (все параметры из .env)."""

    # WooCommerce
    WC_URL: str = ""
    WC_CONSUMER_KEY: str = ""
    WC_CONSUMER_SECRET: str = ""
    WC_WEBHOOK_SECRET: str = ""

    # Мой Склад: авторизация (Bearer Token)
    MS_TOKEN: str = ""

    # UUID сущностей МС (константы)
    MS_ORGANIZATION_ID: str = ""
    MS_STORE_ID: str = ""
    MS_STORE_OPENED_ID: str = ""  # Склад "Вскрытые" (товары из видеообзора)
    MS_CURRENCY_RUB_ID: str = ""
    MS_SALES_CHANNEL_ID: str = ""
    MS_STATE_NEW_LEAD_ID: str = ""

    # UUID справочников (customentity)
    MS_CUSTOMENTITY_DELIVERY_SD_ID: str = ""
    MS_CUSTOMENTITY_DELIVERY_TYPE_ID: str = ""
    MS_CUSTOMENTITY_PAYMENT_TYPE_ID: str = ""

    # UUID доп. полей заказа покупателя
    MS_ATTR_ORDER_NUMBER_ID: str = ""
    MS_ATTR_PAYMENT_METHOD_ID: str = ""
    MS_ATTR_PROMO_CODE_ID: str = ""
    MS_ATTR_DELIVERY_SD_ID: str = ""
    MS_ATTR_DELIVERY_TYPE_ID: str = ""
    MS_ATTR_PVZ_CODE_ID: str = ""
    MS_ATTR_DELIVERY_COST_ID: str = ""
    MS_ATTR_ESTIMATED_COST_ID: str = ""
    MS_ATTR_TOTAL_TO_PAY_ID: str = ""
    MS_ATTR_PAYMENT_TYPE_ID: str = ""
    MS_ATTR_COURIER_COMMENT_ID: str = ""

    # UUID элементов справочника "Прием платежа"
    MS_PAYMENT_TYPE_PREPAID_ID: str = ""
    MS_PAYMENT_TYPE_NONCASH_ID: str = ""

    # UUID элементов справочника "Доставка (СД)"
    MS_DELIVERY_SD_CDEK_ID: str = ""
    MS_DELIVERY_SD_YANDEX_ID: str = ""
    MS_DELIVERY_SD_PICKUP_ID: str = ""

    # UUID элементов справочника "Вид доставки"
    MS_DELIVERY_TYPE_PVZ_ID: str = ""
    MS_DELIVERY_TYPE_POSTAMAT_ID: str = ""
    MS_DELIVERY_TYPE_COURIER_ID: str = ""

    # InSales (опционально)
    INSALES_SHOP_URL: str = ""
    INSALES_API_KEY: str = ""
    INSALES_PASSWORD: str = ""

    # МС: InSales-специфичные UUID
    MS_ORGANIZATION_INSALES_ID: str = ""
    MS_STATE_INSALES_NEW_ID: str = ""
    MS_PROJECT_INSALES_ID: str = ""

    # Настройки
    MS_MAX_REQUESTS_PER_SECOND: int = 3
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    LOG_LEVEL: str = "INFO"


def load_config(env_path: str | None = None) -> Config:
    """Загрузить конфигурацию из .env файла и валидировать обязательные поля."""
    load_dotenv(env_path or ".env")

    config = Config()

    # Заполнить все поля из переменных окружения
    for field_name in config.__dataclass_fields__:
        env_val = os.getenv(field_name)
        if env_val is not None:
            field_type = type(getattr(config, field_name))
            if field_type == int:
                setattr(config, field_name, int(env_val))
            else:
                setattr(config, field_name, env_val)

    # Инициализация логирования
    setup_logging(config.LOG_LEVEL)

    # Валидация обязательных полей
    required = [
        "WC_URL", "WC_CONSUMER_KEY", "WC_CONSUMER_SECRET",
        "MS_ORGANIZATION_ID", "MS_STORE_ID",
    ]

    # Токен обязателен
    if not config.MS_TOKEN:
        raise ValueError("Необходимо указать MS_TOKEN")

    missing = [f for f in required if not getattr(config, f)]
    if missing:
        raise ValueError(f"Отсутствуют обязательные переменные: {', '.join(missing)}")

    # Предупреждения о незаполненных UUID доп. полей
    attr_fields = [f for f in config.__dataclass_fields__ if f.startswith("MS_ATTR_")]
    for f in attr_fields:
        if not getattr(config, f):
            log.warning("UUID доп. поля не задан", field=f)

    return config
