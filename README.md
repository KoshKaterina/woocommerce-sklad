# WooCommerce + InSales → Мой Склад

Односторонняя интеграция: при создании заказа в WooCommerce **или** InSales данные передаются в Мой Склад (создаётся Заказ покупателя). При получении оплаты — проставляется входящий платёж. Существующие заказы в МС никогда не перезаписываются — менеджеры правят их вручную.

Python 3.10+ / FastAPI / structlog

---

## Архитектура

Два источника заказов (WC, InSales) → единый внутренний формат `NormalizedOrder` → `OrderProcessor` → API МойСклад.

```
WC order_data     ─┐                              ┌─► find_or_create контрагент
                   ├─► NormalizedOrder ─► Order-  ├─► build_positions
InSales order_data ─┘   (wc/insales_normalizer)   │   Processor      │
                                                   └─► POST /customerorder
```

Код разбит на пакеты по фичам (см. «Структура проекта» ниже): общий конвейер — `core/`, источники — `woocommerce/`, `insales/`, `ucoz/`.

Ключевые файлы:
- `core/normalized_order.py` — `NormalizedOrder`, `NormalizedCustomer`, `NormalizedLineItem`, `NormalizedDeliveryService`
- `woocommerce/normalizer.py` / `insales/normalizer.py` / `ucoz/normalizer.py` — нормализация сырых заказов → `NormalizedOrder`
- `core/order_processor.py` — универсальный `process_normalized_order`; точки входа `process_order` (WC), `process_insales_order`, `process_ucoz_order`
- `core/source_adapter.py` — `WooSourceAdapter`, `InSalesSourceAdapter`: унифицированный интерфейс для `Reconciliation`
- `core/reconciliation.py` — периодическая сверка по всем адаптерам (раз в 3 мин)

InSales-специфика:
- **Family Pack** (`TG-FP{COLOR}`) → одна позиция InSales → **2 позиции** в МС с поделённой поровну ценой
- **COD margin** → отдельная услуга-позиция "Наценка за наложенный платеж"
- **Организация** для InSales-заказов — ИП Абовян (`MS_ORGANIZATION_INSALES_ID`)
- **SKU-маппинг** для Family Pack и пустых SKU захардкожен в `insales/normalizer.py` (`_INSALES_SKU_TO_MS_SKUS` / `_INSALES_VARIANT_ID_TO_MS_SKUS`)

---

## Установка

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

---

## Настройка

Заполнить `.env`:

```env
# WooCommerce
WC_URL=https://your-shop.com
WC_CONSUMER_KEY=ck_...
WC_CONSUMER_SECRET=cs_...
WC_WEBHOOK_SECRET=...            # опционально, для HMAC-верификации

# Мой Склад
MS_TOKEN=...

# UUID сущностей МС (основное)
MS_ORGANIZATION_ID=...           # WC-организация (дефолт)
MS_STORE_ID=...
MS_STORE_OPENED_ID=...           # склад "Вскрытые" (товары из видеообзора)
MS_CURRENCY_RUB_ID=...
MS_SALES_CHANNEL_ID=...
MS_STATE_NEW_LEAD_ID=...

# UUID справочников и доп. полей — см. config.py (MS_ATTR_*, MS_CUSTOMENTITY_*, MS_DELIVERY_*, MS_PAYMENT_*)

# InSales (опционально — без этих переменных InSales-адаптер отключится)
INSALES_SHOP_URL=myshop.myinsales.ru
INSALES_API_KEY=...
INSALES_PASSWORD=...

MS_ORGANIZATION_INSALES_ID=...   # ИП Абовян
MS_STATE_INSALES_NEW_ID=...      # статус "Новый" для InSales-заказов (опционально)
MS_PROJECT_INSALES_ID=...        # проект в МС для InSales (опционально)

# uCoz / TG-магазин (опционально — без UCOZ_POLL_URL поллер не запустится)
UCOZ_POLL_URL=https://sunscrypt.usite.pro/php/uamo.php
UCOZ_STATE_PATH=data/ucoz_state.json
UCOZ_POLL_INTERVAL_SECONDS=60
```

Для получения UUID из МС:

```bash
python -m woo_moysklad.setup_ms_ids
```

---

## Запуск

```bash
# FastAPI сервер: /webhook/order, /health, периодическая сверка каждые 3 мин
uvicorn woo_moysklad.main:app --host 0.0.0.0 --port 8000

# Ручная передача заказа WC в МС
python scripts/process_order.py 15674

# Ручная передача заказа InSales в МС (по номеру или id, либо из JSON-файла)
python scripts/process_insales_order.py 17665
python scripts/process_insales_order.py tests/fixtures/insales_sample_order.json
```

---

## Тесты

```bash
python -m pytest tests/ -v
```

Сейчас ~163 теста (uCoz-тесты — WIP, вне коммита):
- `test_field_mappers.py` — маппинг полей WC, банковский перевод (`is_manual_prepayment`)
- `test_address_parser.py` — разбор адреса на компоненты (shipmentAddressFull) на реальных образцах
- `test_wc_normalizer.py` — нормализатор WC (бесплатная СДЭК, is_paid)
- `test_counterparty.py` — `split_full_name`, `normalize_phone`, find/create + обогащение имени/почты
- `test_order_processor.py` — универсальный путь через `NormalizedOrder`, `_to_ms_moment`
- `test_insales_normalizer.py` — нормализатор InSales (SKU-маппинг, Family Pack, промокод, доставка, COD)
- `test_insales_e2e.py` — обработка реальных фикстур InSales до POST в МС (моки только для `MoySkladClient`)
- `test_reconciliation.py` — унифицированная сверка через `SourceAdapter`
- `test_field_resync.py` — обратная синхронизация полей (категоризация оплаты, пересчёт, идемпотентность)
- `test_ucoz.py`, `test_ucoz_normalizer.py` — uCoz (WIP, вне коммита)

---

## Структура проекта

```
woo_moysklad/                  — пакет приложения (только он копируется в Docker-образ)
  main.py                  — FastAPI: /webhook/order, /health, lifespan (точка входа)
  setup_ms_ids.py          — утилита получения UUID из МС

  config.py                — конфигурация из .env
  logger.py                — structlog (stdout + файл с ротацией)
  exceptions.py            — CounterpartyError, OrderProcessingError, MoySkladAPIError
  ms_client.py             — HTTP-клиент МС (rate limiter, retry) — целевая система, общая

  core/                    — платформо-независимый конвейер заказа
    normalized_order.py    — формат NormalizedOrder, NormalizedCustomer, ...
    order_processor.py     — сборка и отправка заказа в МС (process_normalized_order)
    counterparty_handler.py— поиск/создание контрагента по телефону
    product_matcher.py     — сопоставление товаров по SKU, услуги доставки
    field_mappers.py       — маппинг полей → МС (плоский адрес, тип доставки, ПВЗ, промокод, доп.поля)
    address_parser.py      — разбор адреса на компоненты для нативного shipmentAddressFull
                             (индекс/страна/город/улица/дом/квартира), ISO→страна
    reconciliation.py      — периодическая сверка по всем адаптерам (раз в 3 мин, окно 9 мин)
    source_adapter.py      — WooSourceAdapter, InSalesSourceAdapter для Reconciliation
    field_resync.py        — обратная синхронизация доп.полей при ручных правках
                             менеджера (TODO §4; вкл по умолч., FIELD_RESYNC_ENABLED)

  woocommerce/             — источник WooCommerce
    client.py              — клиент WooCommerce REST API v3
    normalizer.py          — WC order_data → NormalizedOrder

  insales/                 — источник InSales
    client.py              — клиент InSales API (Basic Auth, rate limit 500/5мин)
    normalizer.py          — InSales order_data → NormalizedOrder + SKU-маппинг

  ucoz/                    — источник uCoz (TG-магазин) — WIP, НЕ в коммите (untracked)
    client.py              — клиент uamo.php (один последний заказ, без авторизации)
    normalizer.py          — uCoz uamo.php → NormalizedOrder + goods_id-маппинг
    poller.py              — отдельный 1-минутный таймер для uCoz (вне Reconciliation)
    state.py               — JSON-стейт last_processed_ucoz_order_id

scripts/                       — ручные/разовые утилиты (НЕ входят в Docker-образ)
  process_order.py         — ручная передача одного заказа WC в МС
  process_insales_order.py — ручная передача одного заказа InSales в МС
  check_delivery_stats.py, check_insales_skus.py, compare_products.py,
  fetch_insales_test_data.py, fetch_ucoz_test_data.py
                           — разовые аналитические утилиты/сбор фикстур (настройка маппингов)

tests/
  test_field_mappers.py, test_address_parser.py, test_counterparty.py,
  test_order_processor.py, test_insales_normalizer.py, test_insales_e2e.py,
  test_reconciliation.py, test_ucoz.py, test_ucoz_normalizer.py
  fixtures/                — реальные заказы WC/InSales/uCoz, payment gateways, webhooks

docs/reference/                — справочные данные
  ucoz_goods.csv           — экспорт товаров uCoz (242 позиции), исходник для goods_id-маппинга
```

---

## Статус интеграции

- ✅ WC-поток в проде, работает по вебхукам + сверка раз в 3 мин
- 🟢 InSales-поток: код готов, покрыт тестами, работает через сверку; канал продаж TangemShop, организация ИП Абовян. Вебхук `/webhook/insales/order` — отложен (см. `TODO.md`)
- 🟢 Обратная синхронизация полей из МС (TODO §4): реализована (`core/field_resync.py`), **включена по умолчанию** (`FIELD_RESYNC_ENABLED=true`; отключить — `=false` в `.env`). Пересчитывает зависимые доп.поля при ручных правках менеджера; исключает канал «Маркетплейс». Тест на 1 заказе: `python scripts/resync_order.py --order <N> --dry-run`
- 🟡 uCoz-поток (TG-магазин): код готов, но **в WIP — не закоммичен** (пакет `woo_moysklad/ucoz/`). `main.py` импортирует его лениво (только при заданной `UCOZ_POLL_URL`)
- ⚠️ Доп.поля МС обновлены (2026-06): «Вид доставки» теперь `long`, стоимости — `double`, «Прием платежа» — новый справочник. UUID новых полей/каналов и параметры InSales-источника (кроме API-ключа/пароля) **захардкожены** в `config.py` (`_HARDCODED_DEFAULTS`, не из `.env`). Прод-`.env` для InSales нужны только `INSALES_API_KEY` и `INSALES_PASSWORD`
- 🟢 Разнесение адреса доставки (2026-06, WC): кроме плоского `shipmentAddress` заполняем нативный объект `shipmentAddressFull` (индекс/страна/город/улица/дом/квартира). Источник — стандартизованный плагином DaData `shipping.address_1` (курьер) / `address_2` + мета CDEK (ПВЗ), парсится локально (`core/address_parser.py`). Код ПВЗ — из меты `_official_cdek_office_code` (первично). Страна резолвится в справочник МС по ISO-коду (`ms_client.find_country_meta`, не хардкод — бывают KZ/BY); регион не пишем, «Другое» (addInfo) не заполняем. Самовывоз из офиса — без адреса. Прод-заказы 8–11.06 вычищены ретро-скриптом (`scripts/fix_address_retro.py`). InSales — отдельным шагом, пока не сделан

Актуальные задачи и ограничения: `TODO.md`, `KNOWN_ISSUES.md`.

> Файл `integration_woo_moysklad_docs_v2.md` — **историческая** документация v2.0 (до InSales-рефакторинга). Использовать только для понимания решений, принятых на этапе WC-only. Актуальная архитектура — выше и в коде.
