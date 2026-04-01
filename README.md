# WooCommerce → Мой Склад

Односторонняя интеграция: при создании заказа в WooCommerce данные передаются в Мой Склад (создаётся Заказ покупателя).

Python 3.10+ / FastAPI / structlog

## Установка

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Настройка

Скопировать `.env.example` → `.env` и заполнить:

```env
# WooCommerce
WC_URL=https://your-shop.com
WC_CONSUMER_KEY=ck_...
WC_CONSUMER_SECRET=cs_...

# Мой Склад (Bearer Token)
MS_TOKEN=...

# UUID сущностей МС (организация, склад, валюта, канал продаж, статус)
MS_ORGANIZATION_ID=...
MS_STORE_ID=...
MS_CURRENCY_RUB_ID=...
MS_SALES_CHANNEL_ID=...
MS_STATE_NEW_LEAD_ID=...
```


Для получения UUID из МС:

```bash
python -m woo_moysklad.setup_ms_ids
```

## Запуск

```bash
# FastAPI сервер (вебхук + health check)
uvicorn woo_moysklad.main:app --host 0.0.0.0 --port 8000

# Ручная передача заказа в МС
python process_order.py 12345
```

## Тесты

```bash
python -m pytest tests/ -v
```

## Структура проекта

```
woo_moysklad/
  config.py               — конфигурация из .env
  logger.py               — structlog (stdout + файл с ротацией)
  exceptions.py           — CounterpartyError, OrderProcessingError, MoySkladAPIError
  ms_client.py            — HTTP-клиент МС (rate limiter, retry)
  woo_client.py           — клиент WooCommerce API
  counterparty_handler.py — поиск/создание контрагента по телефону
  field_mappers.py        — маппинг полей WC → МС
  product_matcher.py      — сопоставление товаров по SKU, услуги доставки
  order_processor.py      — сборка и отправка заказа в МС
  reconciliation.py       — периодическая сверка (раз в 20 минут)
  main.py                 — FastAPI: /webhook/order, /health
  setup_ms_ids.py         — утилита получения UUID из МС
tests/
  test_field_mappers.py
  test_counterparty.py
  test_order_processor.py
```
