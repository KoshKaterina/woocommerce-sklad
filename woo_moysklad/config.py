# Конфигурация интеграции: чтение .env, валидация обязательных полей

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

from woo_moysklad.logger import get_logger, setup_logging

log = get_logger(__name__)

# Поля с захардкоженными дефолтами конкретного аккаунта/источника: НЕ читаются из .env
# (мигрированные UUID полей/каналов 2026-06 + параметры InSales-источника). Чтобы не
# править прод-.env при деплое и чтобы старые значения в .env их не перебивали.
# Секреты (токены, INSALES_API_KEY/PASSWORD) сюда НЕ входят — они только из .env.
_HARDCODED_DEFAULTS = frozenset({
    "MS_ATTR_DELIVERY_TYPE_ID", "MS_ATTR_DELIVERY_COST_ID", "MS_ATTR_ESTIMATED_COST_ID",
    "MS_ATTR_TOTAL_TO_PAY_ID", "MS_ATTR_PAYMENT_TYPE_ID", "MS_ATTR_COURIER_COMMENT_ID",
    "MS_CUSTOMENTITY_PAYMENT_TYPE_ID", "MS_PAYMENT_TYPE_PREPAID_ID", "MS_PAYMENT_TYPE_NONCASH_ID",
    "MS_SALES_CHANNEL_INSALES_ID", "MS_SALES_CHANNEL_MARKETPLACE_ID",
    "MS_DELIVERY_SD_DOSTAVISTA_ID", "MS_DELIVERY_SD_SHOWROOM_ID",
    "MS_DELIVERY_SD_RMS_PICKUP_ID",
    "INSALES_SHOP_URL", "MS_ORGANIZATION_INSALES_ID", "MS_STATE_INSALES_NEW_ID",
    "MS_PROJECT_INSALES_ID",
})


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
    MS_CUSTOMENTITY_PAYMENT_TYPE_ID: str = "00a648ac-60ac-11f1-0a80-1cc60006b0c8"  # захардкожено
    # Примечание: «Вид доставки» с 2026-06 — поле типа long (0-5), не справочник

    # UUID доп. полей заказа покупателя — из .env (зависят от аккаунта, не менялись):
    MS_ATTR_ORDER_NUMBER_ID: str = ""
    MS_ATTR_PAYMENT_METHOD_ID: str = ""
    MS_ATTR_PROMO_CODE_ID: str = ""
    MS_ATTR_DELIVERY_SD_ID: str = ""
    MS_ATTR_PVZ_CODE_ID: str = ""
    # ...а эти — захардкожены (мигрированные поля 2026-06, НЕ из .env; см. _HARDCODED_DEFAULTS):
    MS_ATTR_DELIVERY_TYPE_ID: str = "8c337f77-5d2b-11f1-0a80-1cae0026fe2e"
    MS_ATTR_DELIVERY_COST_ID: str = "6197cf57-5d04-11f1-0a80-0e1800256067"
    MS_ATTR_ESTIMATED_COST_ID: str = "6197d336-5d04-11f1-0a80-0e1800256068"
    MS_ATTR_TOTAL_TO_PAY_ID: str = "80814b14-5d04-11f1-0a80-1d5a00242f6e"
    MS_ATTR_PAYMENT_TYPE_ID: str = "574102c9-60ac-11f1-0a80-0e5500051d84"
    # «Комментарий курьеру» — НЕ заполняется интеграцией (менеджер пишет вручную).
    # Комментарий покупателя идёт только в нативное поле «Комментарий» (description).
    MS_ATTR_COURIER_COMMENT_ID: str = "ed537fe2-5d04-11f1-0a80-0e18002576eb"

    # UUID элементов справочника "Прием платежа" — захардкожено
    MS_PAYMENT_TYPE_PREPAID_ID: str = "0db95b3b-60ac-11f1-0a80-1b9f0005d237"   # "1"
    MS_PAYMENT_TYPE_NONCASH_ID: str = "16bb90ce-60ac-11f1-0a80-11190005b58b"   # "2"

    # UUID элементов справочника "Доставка (СД)".
    # Интеграция ставит автоматически ТОЛЬКО эти значения (WC: СДЭК / Достависта /
    # шоурум; InSales: СДЭК / ExpressRMS-самовывоз). Остальные элементы справочника
    # (ускоренные/экспресс-тарифы, Яндекс, Почта России и пр.) менеджер выбирает
    # вручную — код их не трогает.
    MS_DELIVERY_SD_CDEK_ID: str = ""  # из .env (исторически)
    # ...новые элементы 2026-06 — захардкожены (см. _HARDCODED_DEFAULTS):
    MS_DELIVERY_SD_DOSTAVISTA_ID: str = "5b457631-633a-11f1-0a80-1a4500355b37"  # Достависта (стандартная)
    MS_DELIVERY_SD_SHOWROOM_ID: str = "66d0d59d-65a3-11f1-0a80-045b001337ad"    # Самовывоз из шоурума Sunscrypt
    MS_DELIVERY_SD_RMS_PICKUP_ID: str = "4bd63ae7-2aa1-11f1-0a80-023a0030436b"  # ExpressRMS(Самовывоз), InSales-шоурум

    # «Вид доставки» теперь long-поле: коды (1=ПВЗ, 2=курьер, 3=почтомат)
    # зашиты в OrderProcessor._resolve_delivery_type_num — отдельные UUID не нужны

    # InSales: только ключ и пароль — из .env (секреты). Остальное захардкожено.
    INSALES_API_KEY: str = ""
    INSALES_PASSWORD: str = ""
    INSALES_SHOP_URL: str = "tangemshop.ru"  # захардкожено

    # МС: InSales-специфичные UUID — захардкожено (см. _HARDCODED_DEFAULTS)
    MS_ORGANIZATION_INSALES_ID: str = "20413198-d891-11ef-0a80-10c20004fabc"  # ИП Абовян
    MS_STATE_INSALES_NEW_ID: str = "0f062083-c413-11ee-0a80-13fd002f64a5"
    MS_PROJECT_INSALES_ID: str = "0fd3c635-d892-11ef-0a80-03c300056ff7"
    MS_SALES_CHANNEL_INSALES_ID: str = "77525ff2-60be-11f1-0a80-0d620009ec3b"  # TangemShop

    # uCoz (опционально)
    UCOZ_POLL_URL: str = ""
    UCOZ_STATE_PATH: str = "data/ucoz_state.json"
    UCOZ_POLL_INTERVAL_SECONDS: int = 60

    # Обратная синхронизация полей (reverse-sync, TODO §4)
    FIELD_RESYNC_ENABLED: bool = True            # включено; отключить — FIELD_RESYNC_ENABLED=false в .env
    MS_SALES_CHANNEL_MARKETPLACE_ID: str = "25a34be1-54e2-11ef-0a80-0c7c00196716"  # «Маркетплейс», захардкожено

    # Настройки
    MS_MAX_REQUESTS_PER_SECOND: int = 3
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    LOG_LEVEL: str = "INFO"


def load_config(env_path: str | None = None) -> Config:
    """Загрузить конфигурацию из .env файла и валидировать обязательные поля."""
    load_dotenv(env_path or ".env")

    config = Config()

    # Заполнить все поля из переменных окружения (кроме захардкоженных UUID)
    for field_name in config.__dataclass_fields__:
        if field_name in _HARDCODED_DEFAULTS:
            continue
        env_val = os.getenv(field_name)
        if env_val is not None:
            field_type = type(getattr(config, field_name))
            if field_type == bool:
                setattr(config, field_name, env_val.strip().lower() in ("1", "true", "yes", "on"))
            elif field_type == int:
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
