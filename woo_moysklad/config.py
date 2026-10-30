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
    "MS_ATTR_COURIER_COMMENT_ID", "MS_ATTR_YM_CLIENT_ID", "MS_ATTR_COUNTERPARTY_TELEGRAM_ID",
    "MS_ATTR_ORDER_TYPE_ID", "MS_DICT_ORDER_TYPE_ID",
    "MS_DICT_ORDER_TYPE_ORDER_ID", "MS_DICT_ORDER_TYPE_PREORDER_ID",
    "MS_SALES_CHANNEL_INSALES_ID", "MS_SALES_CHANNEL_MARKETPLACE_ID",
    "INSALES_SHOP_URL", "MS_ORGANIZATION_INSALES_ID", "MS_STATE_INSALES_NEW_ID",
    "MS_PROJECT_INSALES_ID", "MS_ATTR_CHECKOUT_CONSENTS_ID", "MS_ATTR_SITE_CLIENT_ID",
    "INSALES_MAIN_WALLET_PRODUCT_ID",
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
    # Статус для заказов с самовывозом из офиса Sunscrypt (WC local_pickup / «showroom»).
    # Если задан — такие заказы создаются в этом статусе вместо MS_STATE_NEW_LEAD_ID.
    MS_STATE_SHOWROOM_PICKUP_ID: str = ""

    # UUID доп. полей заказа покупателя — из .env (зависят от аккаунта, не менялись):
    MS_ATTR_ORDER_NUMBER_ID: str = ""
    MS_ATTR_PAYMENT_METHOD_ID: str = ""
    MS_ATTR_PROMO_CODE_ID: str = ""
    MS_ATTR_PVZ_CODE_ID: str = ""
    # Новое доп. поле Заказа покупателя типа «Текст» (многострочное): пусто
    # до создания поля и сквозного теста — старый поток без изменений.
    MS_ATTR_PREORDER_SUMMARY_ID: str = ""
    # «Согласия с сайта» (text), created 19.10.2026; only versioned snapshots are sent.
    MS_ATTR_CHECKOUT_CONSENTS_ID: str = "327bfdfa-cbd4-11f1-0a80-1c9d00f0e1bf"
    # «Клиент с сайта» (text), доп.поле Заказа покупателя: имя, телефон, почта, адрес и комментарий
    # как их ввёл клиент. Читает amo_fix_fields (заметка в сделку). Создано 29.10.2026,
    # захардкожено (поле наше, от аккаунта не зависит). Пусто - запись выключена.
    MS_ATTR_SITE_CLIENT_ID: str = "51533fa0-d36d-11f1-0a80-0e48001ce0ed"
    # («Доставка» - MS_ATTR_DELIVERY_SD_ID - удалена 06.08.2026, см. ниже)
    # ...а эти — захардкожены (мигрированные поля 2026-06, НЕ из .env; см. _HARDCODED_DEFAULTS).
    # ⚠️ 05.08.2026 удалены поля фулфилмента: «Вид доставки», «Стоимость доставки»,
    # «Оценочная стоимость», «Итого к оплате получателем», «Прием платежа» — вместе
    # с их UUID и константами справочника приёма платежа.
    # «Комментарий курьеру» — НЕ заполняется интеграцией (менеджер пишет вручную).
    # Комментарий покупателя идёт только в нативное поле «Комментарий» (description).
    MS_ATTR_COURIER_COMMENT_ID: str = "ed537fe2-5d04-11f1-0a80-0e18002576eb"
    # «id (для метрики)» — ClientID Яндекс.Метрики из заказа WC (meta_data["ym_client_id"]);
    # сквозная аналитика МС → amoCRM → Метрика. Тип поля — string (19-значное число
    # пишем строкой, без потери точности). Захардкожено (поле создано 2026-06).
    MS_ATTR_YM_CLIENT_ID: str = "26b2eac0-6806-11f1-0a80-045b00304eed"
    # «Телеграм» (string) — доп. поле КОНТРАГЕНТА (не заказа), создано 15.09.2026 по решению
    # Кати: ник принадлежит человеку. Ник «@username» из поля оформления сайта
    # (meta_data["_billing_telegram"]); новому контрагенту пишется сразу, существующему —
    # только в пустое поле. Захардкожено.
    MS_ATTR_COUNTERPARTY_TELEGRAM_ID: str = "79e2dacf-b10e-11f1-0a80-1075003b0415"
    # «Тип заявки» (customentity) — доп. поле ЗАКАЗА, заведено 18.09.2026 по решению Кати
    # вместе со своим справочником. Его читает сторонний мост amgroup и кладёт значение в
    # одноимённое поле сделки amoCRM: до этого мост всегда писал туда «Заказ», а «Предзаказ»
    # менеджеры проставляли руками, иногда спустя часы (разбор — WORKLOG.md, 18.09.2026).
    # В справочнике ровно два значения: резерв у нас отдельная сущность, а не тип заявки.
    # Захардкожено по образцу соседей выше — поля наши, от аккаунта не зависят и не меняются.
    MS_ATTR_ORDER_TYPE_ID: str = "895b4791-b344-11f1-0a80-1b9e0028c446"
    MS_DICT_ORDER_TYPE_ID: str = "7706b36b-b344-11f1-0a80-1c6f0026b459"
    MS_DICT_ORDER_TYPE_ORDER_ID: str = "804465e9-b344-11f1-0a80-107d0028b661"
    MS_DICT_ORDER_TYPE_PREORDER_ID: str = "80706b9d-b344-11f1-0a80-107d0028b67a"
    # ⚠️ 06.08.2026 удалено поле «Доставка» (customentity) вместе со справочником
    # служб доставки и всеми его элементами (MS_ATTR_DELIVERY_SD_ID,
    # MS_CUSTOMENTITY_DELIVERY_SD_ID, MS_DELIVERY_SD_*): вид доставки живёт в услугах
    # заказа, отдельное поле не нужно (решение Кати).

    # «Вид доставки» теперь long-поле: коды (1=ПВЗ, 2=курьер, 3=почтомат)
    # зашиты в OrderProcessor._resolve_delivery_type_num — отдельные UUID не нужны

    # InSales: только ключ и пароль — из .env (секреты). Остальное захардкожено.
    INSALES_API_KEY: str = ""
    INSALES_PASSWORD: str = ""
    INSALES_SHOP_URL: str = "tangemshop.ru"  # захардкожено

    # МС: InSales-специфичные UUID — захардкожено (см. _HARDCODED_DEFAULTS)
    MS_ORGANIZATION_INSALES_ID: str = "20413198-d891-11ef-0a80-10c20004fabc"  # ИП Абовян
    # Стартовый статус заказа InSales. ⚠️ Менять только вместе с таблицей соответствий
    # моста amgroup (amoCRM → Настройки → Интеграции → «МойСклад интеграция» → «Статусы»):
    # по ЭТОМУ статусу мост выбирает воронку для новой сделки. До 30.09.2026 здесь стояла
    # «Первичка» (0f062083-c413-11ee-0a80-13fd002f64a5), которой в таблице моста нет ни у
    # одной из десяти воронок - и сделки Tangemshop падали в «ОП розница», к розничному
    # распределению (разбор - projects/amo-cleanup/JOURNAL.md, 30.09).
    MS_STATE_INSALES_NEW_ID: str = "f5c6ba6b-668c-11f1-0a80-0fd50022d85e"  # TangemShop Новый заказ
    MS_PROJECT_INSALES_ID: str = "0fd3c635-d892-11ef-0a80-03c300056ff7"
    MS_SALES_CHANNEL_INSALES_ID: str = "77525ff2-60be-11f1-0a80-0d620009ec3b"  # TangemShop

    # «Тип заявки» у заказов InSales: Заказ / Предзаказ по резерву платформы
    # (разбор - woo_moysklad/insales/preorder.py). Выключено по умолчанию:
    # включать INSALES_ORDER_TYPE_ENABLED=true в .env.
    INSALES_ORDER_TYPE_ENABLED: bool = False
    # Предел возраста заказа, при котором резерву ещё можно верить. Штатный путь -
    # сверка раз в 3 минуты с отступом 2 минуты на свежие заказы, то есть заказ
    # обрабатывается за ~5 минут. Двадцать минут - запас, который при этом отсекает
    # догонку после сбоя связи (она берёт заказы возрастом до шести часов).
    INSALES_PREORDER_MAX_AGE_MINUTES: int = 20

    # Акция «дополнительный белый Tangem 2.0 (3 карты) за 4 990 ₽» (ТЗ 30.10.2026):
    # вариант скрытого акционного товара InSales. 0 - товар не заведён, проверка права на
    # акцию выключена. Задать в .env после создания товара: INSALES_EXTRA_WHITE_VARIANT_ID.
    # Разбор - woo_moysklad/insales/extra_white.py.
    INSALES_EXTRA_WHITE_VARIANT_ID: int = 0
    # Окончание акции, ISO с поясом (2026-11-30T23:59:59+03:00). Пусто - срок не проверяется.
    INSALES_EXTRA_WHITE_ENDS_AT: str = ""
    # «Tangem 2.0»: любой его вариант даёт право на один акционный набор (захардкожено).
    INSALES_MAIN_WALLET_PRODUCT_ID: int = 318114475

    # uCoz (опционально)
    UCOZ_POLL_URL: str = ""
    UCOZ_STATE_PATH: str = "data/ucoz_state.json"
    UCOZ_POLL_INTERVAL_SECONDS: int = 60

    # Обратная синхронизация полей (reverse-sync, TODO §4)
    FIELD_RESYNC_ENABLED: bool = True            # включено; отключить — FIELD_RESYNC_ENABLED=false в .env
    MS_SALES_CHANNEL_MARKETPLACE_ID: str = "25a34be1-54e2-11ef-0a80-0c7c00196716"  # «Маркетплейс», захардкожено

    # Промо Tangemshop «первый кошелёк Family Pack всегда белый» (01.09.2026):
    # выключено по умолчанию — Family Pack раскладывается как раньше (1 чёрный + 1 по цвету).
    # Включить на время акции — FAMILY_PACK_WHITE_FIRST_ENABLED=true в .env. См.
    # _INSALES_SKU_TO_MS_SKUS_WHITE_FIRST в woo_moysklad/insales/normalizer.py.
    FAMILY_PACK_WHITE_FIRST_ENABLED: bool = False

    # Разбивка цены Family Pack между 2 позициями МС при включённом промо (см. выше):
    # белый (первая позиция) записывается по цене акции — база минус скидка, а не
    # «поровну». База — живая цена белого 3-карточного (TG130X3-B) на момент акции
    # (01.09.2026: 7 590 ₽), проверять/менять в .env, если цену в InSales пересмотрят,
    # деплой без правки кода не нужен. См. _split_family_pack_price в normalizer.py.
    FAMILY_PACK_WHITE_BASE_PRICE_KOPECKS: int = 759000   # 7 590 ₽
    FAMILY_PACK_WHITE_DISCOUNT_PERCENT: int = 30

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
