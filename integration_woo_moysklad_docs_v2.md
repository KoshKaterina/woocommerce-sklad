# Документация интеграции WooCommerce → Мой Склад
**Версия:** 2.0 (на основе реального заказа WC #15674)

---

## 1. Общее описание

Односторонняя интеграция: при создании заказа в WooCommerce данные передаются в Мой Склад (создаётся Заказ покупателя). При смене статуса заказа WC на «Обработка» — проставляется оплата (входящий платёж). Существующие заказы в МС никогда не обновляются и не удаляются интеграцией.

**Язык:** Python 3.10+  
**Фреймворк:** FastAPI (приём вебхуков)  
**Конфигурация:** .env файл  
**Логирование:** structlog → файл + stdout

---

## 2. API сервисов: ключевые особенности

### 2.1 WooCommerce REST API v3

**Базовый URL:** `https://{domain}/wp-json/wc/v3/`  
**Аутентификация:** HTTP Basic Auth (consumer_key : consumer_secret)  
**Библиотека Python:** `woocommerce` (pip install woocommerce)

**Инициализация:**
```python
from woocommerce import API
wcapi = API(
    url="https://shop.example.com",
    consumer_key=os.getenv("WC_CONSUMER_KEY"),
    consumer_secret=os.getenv("WC_CONSUMER_SECRET"),
    wp_api=True,
    version="wc/v3",
    timeout=30
)
```

**Лимиты:**
- Пагинация: 10 элементов по умолчанию, max 100 через `per_page`
- Нет явного rate limit от WooCommerce, но хостинг может ограничивать
- Заголовки пагинации: `X-WP-Total`, `X-WP-TotalPages`

**Вебхуки WooCommerce:**
- Топик `order.created` — при создании заказа
- Топик `order.updated` — при изменении заказа
- Payload: полный JSON объекта заказа
- Верификация: заголовок `X-WC-Webhook-Signature` (HMAC-SHA256 от body, ключ = webhook secret)
- После 5 неудачных доставок вебхук отключается автоматически
- Delivery URL должен быть HTTPS

**Поля заказа WooCommerce — что читаем, что игнорируем:**

| Поле WooCommerce | Читаем | Описание / примечание |
|---|---|---|
| `id` | ✅ | ID заказа → "Номер заказа на сайте" в МС |
| `number` | ❌ | Дублирует `id`, не нужен |
| `status` | ✅ | `"processing"` → проставить оплату; при создании в МС ВСЕГДА ставим статус "Новый лид" |
| `currency` | ❌ | Всегда RUB; в МС задаётся константой |
| `total` | ✅ | → "Итого к оплате получателем" если оплата "При получении" |
| `shipping_total` | ✅ | → доп. поле МС "Стоимость доставки" |
| `payment_method` | ❌ | Технический код; используем только `payment_method_title` |
| `payment_method_title` | ✅ | → доп. поля МС "Способ оплаты" и "Приём платежа" |
| `customer_note` | ✅ | → поле МС `description` |
| `date_created` | ❌ | МС проставляет дату автоматически |
| `date_paid` | ✅ | → поле `moment` входящего платежа в МС |
| `billing.first_name` | ✅ | Содержит полное ФИО ("Екатерина Кошенкова") — см. 3.2 |
| `billing.last_name` | ❌ | Не заполняется в этом магазине |
| `billing.email` | ✅ | → email контрагента в МС |
| `billing.phone` | ✅ | → телефон контрагента; используется для поиска дубликата |
| `billing.address_*` / `city` / `state` / `postcode` / `country` | ❌ | Биллинговый адрес; не передаём |
| `shipping.first_name` / `last_name` | ❌ | Не используются |
| `shipping.address_1` | ✅ | Адрес при курьерской доставке; см. 3.3 |
| `shipping.address_2` | ✅ | Адрес ПВЗ/постамата ("КОД, город, улица"); см. 3.3 |
| `shipping.city` | ✅ | Используется при курьерской доставке |
| `shipping.state` | ❌ | Дублирует city; не передаём |
| `shipping.postcode` | ✅ | Индекс → добавляется в строку адреса |
| `shipping.country` | ❌ | Всегда Россия; в МС задаётся константой |
| `shipping_lines[].method_title` | ✅ | → услуга в МС; определяет тип доставки |
| `shipping_lines[].method_id` | ❌ | Не используется |
| `shipping_lines[].total` | ✅ | → стоимость услуги доставки в МС |
| `shipping_lines[].meta_data` | ❌ | Служебные данные СДЭК; не передаём |
| `fee_lines[]` | ❌ | Не используются |
| `line_items[].product_id` | ❌ | Внутренний ID WC; для поиска используем SKU |
| `line_items[].sku` | ✅ | Артикул → поиск товара в МС |
| `line_items[].name` | ❌ | Не передаём |
| `line_items[].quantity` | ✅ | Количество → позиция в заказе МС |
| `line_items[].price` | ✅ | Цена за 1 шт → позиция в заказе МС (× 100 для копеек) |
| `line_items[].total` | ❌ | Не нужен; цена × количество вычисляется в МС |
| `line_items[].meta_data` | ❌ | Не используется |
| `coupon_codes[]` | ❌ | Отложено; поле в МС не заполнять |
| `meta_data[]` | ❌ | Все meta_data заказа игнорируются |

**Важное наблюдение по структуре `billing`:**  
В этом магазине `billing.first_name` содержит **полное ФИО** ("Екатерина Кошенкова"), а `billing.last_name` — пустое. При создании контрагента: вся строка → `name`, первое слово → `firstName`, второе → `lastName`.

### 2.2 Мой Склад JSON API 1.2

**Базовый URL:** `https://api.moysklad.ru/api/remap/1.2/`  
**Аутентификация:** HTTP Basic Auth (login:password) или Bearer Token  
**Рекомендация:** Bearer Token (POST `/security/token`)

**Лимиты:**
- До 45 запросов за 3 секунды (~15 req/s, ~900 req/min)
- HTTP 429 при превышении, заголовок `Retry-After`
- Лимит payload: 20 МБ
- Пагинация: limit (max 1000), offset

**Ключевые эндпоинты:**

| Эндпоинт | Метод | Назначение |
|---|---|---|
| `/entity/counterparty` | GET, POST, PUT | Контрагенты |
| `/entity/customerorder` | GET, POST, PUT | Заказы покупателей |
| `/entity/organization` | GET | Организации |
| `/entity/store` | GET | Склады |
| `/entity/product` | GET | Товары |
| `/entity/service` | GET, POST | Услуги |
| `/entity/currency` | GET | Валюты |
| `/entity/saleschannel` | GET | Каналы продаж |
| `/entity/paymentin` | POST | Входящие платежи (оплата заказа) |
| `/entity/customerorder/metadata` | GET | Метаданные: статусы, доп. поля |

**Обязательные поля при создании Заказа покупателя:**
- `organization` — meta-ссылка на организацию
- `agent` — meta-ссылка на контрагента

**Структура meta-ссылки:**
```json
{
  "meta": {
    "href": "https://api.moysklad.ru/api/remap/1.2/entity/organization/{uuid}",
    "type": "organization",
    "mediaType": "application/json"
  }
}
```

**Используемые поля Заказа покупателя в МС:**

| Поле МС | Тип | Описание |
|---|---|---|
| `organization` | meta | Организация (обязательно, константа) |
| `agent` | meta | Контрагент (обязательно) |
| `store` | meta | Склад (константа) |
| `state` | meta | Статус "Новый лид" (константа) |
| `salesChannel` | meta | Канал продаж (константа) |
| `rate.currency` | meta | Валюта RUB (константа) |
| `description` | string | Комментарий = `customer_note` из WC |
| `shipmentAddress` | string | Адрес доставки одной строкой |
| `positions` | array | Товары + услуги доставки |
| `attributes` | array | Доп. поля (id + value) |

**Доп. поля заказа в МС (attributes) — UUID подтверждены из тестовых данных:**

| Название поля | Тип МС | UUID | Статус |
|---|---|---|---|
| Номер заказа на сайте | text | `70c4735f-c542-11f0-0a80-1755000e25a7` | ✅ подтверждён |
| Способ оплаты | string | `33735877-ba29-11f0-0a80-1737003bc63e` | ✅ подтверждён |
| Промокод | string | `ffcaeea9-13d2-11f1-0a80-0fd5000f56c4` | ✅ подтверждён |
| Доставка (СД) | customentity | `f887a45c-2aa2-11f1-0a80-1bd0003117b4` | ✅ подтверждён |
| Вид доставки | customentity | `10e587ca-2aa3-11f1-0a80-0f48002ee65f` | ✅ подтверждён |
| Код ПВЗ | string | `308100c4-2aa3-11f1-0a80-01a9002fcf77` | ✅ подтверждён |
| Стоимость доставки | string | `04fee4e9-2aa5-11f1-0a80-0d860032db59` | ✅ подтверждён |
| Оценочная стоимость | string | `04fee891-2aa5-11f1-0a80-0d860032db5a` | ✅ подтверждён |
| Итого к оплате получателем | string | `c5f954c4-2aa3-11f1-0a80-138c003062fd` | ✅ подтверждён |
| Прием платежа | customentity | `cbe57ab4-2aa4-11f1-0a80-1a29003029a1` | ✅ подтверждён |
| Комментарий курьеру | string | `d787efae-2aa4-11f1-0a80-145f0031ba34` | ✅ подтверждён |

> ⚠️ Поля "Стоимость доставки", "Оценочная стоимость", "Итого к оплате получателем" имеют тип `string` в МС (не число). Передавать значения как строку: `"555"`, `"0"` и т.д.

**Доп. поля заказа, которые есть в МС, но НЕ заполняются интеграцией:**

| Название поля | UUID | Причина |
|---|---|---|
| [Сотрудник] | `11327486-6485-11f0-0a80-04620016b807` | Заполняется вручную |
| Консультация | `d4167b69-6858-11f0-0a80-0b130004e42c` | Заполняется вручную |
| Ссылка на сделку | `df3f1a0e-b568-11f0-0a80-022700143553` | Заполняется из AmoCRM |
| Заказ создан через виджет | `df47e01f-b568-11f0-0a80-083d00148d85` | Заполняется виджетом |

**Структура позиции (товар):**
```json
{
  "quantity": 1,
  "price": 683100,
  "discount": 0,
  "vat": 0,
  "assortment": {
    "meta": {
      "href": "https://api.moysklad.ru/api/remap/1.2/entity/product/{uuid}",
      "type": "product",
      "mediaType": "application/json"
    }
  }
}
```

**Структура позиции (услуга доставки):**
```json
{
  "quantity": 1,
  "price": 38200,
  "discount": 0,
  "vat": 0,
  "assortment": {
    "meta": {
      "href": "https://api.moysklad.ru/api/remap/1.2/entity/service/{uuid}",
      "type": "service",
      "mediaType": "application/json"
    }
  }
}
```

**ВАЖНО: Цены в МС в копейках (× 100). WC `"6831"` → МС `683100`.**

**Структура контрагента:**

| Поле | Тип | Откуда |
|---|---|---|
| `name` | string | `billing.first_name` целиком |
| `companyType` | string | Всегда `"individual"` |
| `firstName` | string | Первое слово из `billing.first_name` |
| `lastName` | string | Второе слово |
| `middleName` | string | Третье слово (если есть) |
| `email` | string | `billing.email` |
| `phone` | string | `billing.phone` нормализованный |

**Поиск контрагента:**
```
GET /entity/counterparty?filter=phone={нормализованный_телефон}
```

---

## 3. Маппинг полей WooCommerce → Мой Склад

### 3.1 Стандартные поля заказа

| Данные | Источник WC | Поле МС | Правило |
|---|---|---|---|
| Организация | — | `organization` | UUID "ИП Перфилов" из .env |
| Контрагент | `billing.*` | `agent` | См. раздел 3.2 |
| Склад | — | `store` | UUID "Sunscrypt основной" из .env |
| Статус | — | `state` | UUID "Новый лид" из .env (ВСЕГДА) |
| Канал продаж | — | `salesChannel` | UUID "магазин" из .env |
| Валюта | — | `rate.currency` | UUID RUB из .env |
| Комментарий | `customer_note` | `description` | Прямая передача |
| Адрес доставки | см. 3.3 | `shipmentAddress` | Строка по правилу 3.3 |

### 3.2 Контрагент: логика поиска/создания

**Источник данных:** `billing.first_name` = полное ФИО (одна строка), `billing.last_name` = пустое.

```
1. Нормализовать billing.phone:
   - Убрать пробелы, скобки, дефисы
   - 8XXXXXXXXXX → +7XXXXXXXXXX
   - 7XXXXXXXXXX (без +) → +7XXXXXXXXXX
   - Не удалось нормализовать → использовать как есть, WARNING в лог

2. GET /entity/counterparty?filter=phone={normalized}

3. Найден один:
   - Если companyType != "individual" → PUT: companyType = "individual"
   - Вернуть meta

4. Найдено несколько → взять первого, WARNING в лог

5. Не найден → POST /entity/counterparty:
   - name: billing.first_name (вся строка)
   - firstName: parts[0], lastName: parts[1], middleName: parts[2+] (если есть)
   - companyType: "individual"
   - phone: normalized, email: billing.email

6. Ошибка → CRITICAL лог, выбросить исключение (заказ создать невозможно)
```

**Разбивка ФИО (функция split_full_name):**
```
"Екатерина Кошенкова"   → firstName="Екатерина", lastName="Кошенкова", middleName=""
"Иванов Иван Иванович"  → firstName="Иванов", lastName="Иван", middleName="Иванович"
"Мария"                 → firstName="Мария", lastName="", middleName=""
name (поле МС) = исходная строка целиком
```

### 3.3 Адрес доставки

**Текущая реализация:** одно текстовое поле `shipmentAddress`.  
Разбивка на `shipmentAddressFull.*` — **отложена**.

**Определение типа доставки** по `shipping_lines[0].method_title`:

| Условие | Тип |
|---|---|
| Содержит "Самовывоз" или "самовывоз" или "постамат" | `pvz` |
| Всё остальное | `courier` |

**Формула строки адреса:**

| Тип | Формула |
|---|---|
| `pvz` | `"Россия, " + shipping.address_2 + ", " + shipping.postcode` |
| `courier` | `"Россия, " + shipping.city + ", " + shipping.address_1 + ", " + shipping.postcode` |

**Код ПВЗ** (только при типе `pvz`):
Извлекается регулярным выражением `[A-Z]+\d+` — одна или более заглавных латинских букв, за которыми следуют одна или более цифр. Ищется в любом месте строки `shipping.address_2`.
Примеры: `"MSK2425, Москва, ул. Садовая-Кудринская, 20"` → `"MSK2425"`, `"Москва, SBP892, ул. Ленина"` → `"SBP892"`, `"SO78 текст"` → `"SO78"`.
Если паттерн не найден → `None`, поле не заполняется.

### 3.4 Доп. поля заказа (attributes)

| Данные | Источник WC | Поле МС | Тип МС | Правило |
|---|---|---|---|---|
| Номер заказа на сайте | `id` | "Номер заказа на сайте" | text | Строка |
| Способ оплаты | `payment_method_title` | "Способ оплаты" | string | Строка напрямую |
| Приём платежа | `payment_method_title` | "Прием платежа" | customentity | Маппинг 3.5 (справочник) |
| Доставка (СД) | `shipping_lines[0].method_title` | "Доставка" | customentity | Маппинг 3.4 (справочник) |
| Вид доставки | `shipping_lines[0].method_title` | "Вид доставки" | customentity | Маппинг 3.4 (справочник) |
| Код ПВЗ | `shipping.address_2` | "Код ПВЗ" | string | Только при типе `pvz`; regex `[A-Z]+\d+` |
| Стоимость доставки | `shipping_total`, `payment_method_title` | "Стоимость доставки" | string | Если "На карту" → `"0"`; иначе → `str(shipping_total)` |
| Оценочная стоимость | `line_items[].total` | "Оценочная стоимость" | string | Сумма всех `line_items[].total` как строка |
| Итого к оплате получателем | `total` | "Итого к оплате получателем" | string | Если "При получении" → `str(total)`, иначе `"0"` |
| Комментарий курьеру | `customer_note` | "Комментарий курьеру" | string | Прямая передача (то же значение что и `description`) |
| Промокод | `coupon_codes[0]` | "Промокод" | string | **Отложено** — поле пока не заполнять |

> ⚠️ Числовые значения (стоимости) передаются как **строки** (`string`), не как числа — такой тип зафиксирован в МС.

### 3.5 Маппинг приёма платежа (справочник МС)

Справочник UUID: `f20d738a-2aa3-11f1-0a80-16d100307119`

| `payment_method_title` (lower()) | Элемент справочника МС |
|---|---|
| Содержит "на карту" или "онлайн" | "1. Заказ предоплачен" |
| Содержит "при получении" | "2. Безналичная оплата" |
| Не определено | WARNING, поле не заполнять |

Подтверждённый элемент из реального заказа: `ff9acb1e-2aa3-11f1-0a80-1d3c00312319` = "1. Заказ предоплачен". UUID остальных элементов — получить через `GET /entity/customentity/f20d738a-2aa3-11f1-0a80-16d100307119`.

### 3.6а Маппинг "Доставка (СД)" (справочник МС)

Справочник UUID: `1002cab7-2aa1-11f1-0a80-023a00303f73`  
Логика: поиск нужного элемента справочника по substring в `shipping_lines[0].method_title`.

Подтверждённый элемент из реального заказа: `643a747a-2aa1-11f1-0a80-084b002f0a2c` = "10. СДЭК".  
UUID остальных элементов — получить через `GET /entity/customentity/1002cab7-2aa1-11f1-0a80-023a00303f73`.

| Условие (substring в method_title) | Элемент справочника МС |
|---|---|
| содержит "CDEK" или "СДЭК" | "10. СДЭК" |
| содержит "Курьерская по" или "Доставка курьером по Москве" | "12. Достависта" |
| содержит "Самовывоз из офиса" или "Самовывоз офис" | "13. Самовывоз офис Sunscrypt" |
| всё остальное | не заполнять, WARNING |

> Полный список элементов справочника уточнить через setup_ms_ids.py — реализовывать маппинг только для кодов 10, 12, 13 в текущей версии.

### 3.6б Маппинг "Вид доставки" (справочник МС)

Справочник UUID: `9a5d76d0-2aa1-11f1-0a80-023a003048c5`  
Логика: определяется по `shipping_lines[0].method_title`, коррелирует с типом доставки (`pvz` / `courier`).

Подтверждённый элемент из реального заказа: `e89a509e-2aa1-11f1-0a80-1bd00031011a` = "1 - Пункт выдачи".  
UUID остальных элементов — получить через `GET /entity/customentity/9a5d76d0-2aa1-11f1-0a80-023a003048c5`.

| Условие (substring в method_title) | Элемент справочника МС |
|---|---|
| содержит "Самовывоз" или "ПВЗ" (тип `pvz`) | "1 - Пункт выдачи" |
| содержит "постамат" (тип `pvz`) | "3 - Почтомат" |
| курьерская доставка (тип `courier`) | "2 - Курьерская доставка" |
| всё остальное | не заполнять, WARNING |

> Полный список элементов справочника уточнить через setup_ms_ids.py.

- Поиск: `GET /entity/product?filter=article={sku}`
- Не найден по `article` → попробовать `filter=externalCode={sku}`
- Не найден совсем → ERROR лог, позиция пропускается, заказ создаётся
- Цена: `int(float(price) * 100)` (копейки)
- Количество: `quantity`

**Пример из заказа #15674:**

| SKU | Количество | Цена WC | Цена МС (копейки) |
|---|---|---|---|
| TG130X3-B | 1 | 6831 | 683100 |
| TG-RING | 1 | 13491 | 1349100 |
| TG128X3-B-1 | 1 | 4491 | 449100 |

### 3.7 Услуги доставки (shipping_lines → positions)

Каждый элемент `shipping_lines[]` → **отдельная позиция-услуга** в заказе МС.

- Поиск: `GET /entity/service?filter=name={method_title}`
- Не найдена → **создать** новую услугу (POST /entity/service), WARNING лог
- Цена: зависит от способа оплаты:
  - Если `payment_method_title` содержит "на карту" → цена `0`
  - Иначе → `int(float(total) * 100)` (копейки)
- Количество: всегда 1

**Пример из заказа #15674 (оплата "При получении"):**

| method_title | Стоимость WC | Стоимость МС (копейки) |
|---|---|---|
| "CDEK: Самовывоз (1 дней)" | 382 | 38200 |
| "Наценка за наложенный платеж" | 993 | 99300 |

**При оплате "На карту" — все позиции услуг доставки получают цену `0`.**

---

## 4. Архитектура

### 4.1 Компоненты

```
[WooCommerce] --webhook--> [FastAPI сервер] --API--> [Мой Склад]
                                |
                                ├── POST /webhook/order  (приём вебхуков)
                                │     ├── Дедупликация (order_id + action, TTL 5 мин)
                                │     ├── asyncio.Lock (последовательная обработка)
                                │     ├── order.created → создать заказ в МС
                                │     └── order.updated + status=processing → mark_paid (входящий платёж)
                                ├── GET  /health         (liveness probe)
                                └── threading.Timer      (сверка каждые 20 мин)
```

### 4.2 Структура проекта

```
woo_moysklad/
├── .env
├── .env.example
├── requirements.txt
├── main.py                  # FastAPI: вебхук, health, запуск планировщика
├── config.py                # dataclass Config, чтение .env, валидация
├── woo_client.py            # Клиент WC API (для сверки)
├── ms_client.py             # Клиент МС API (rate limiter, retry)
├── order_processor.py       # Главная логика обработки заказа
├── field_mappers.py         # Маппинг полей и трансформации
├── counterparty_handler.py  # Поиск/создание контрагента
├── product_matcher.py       # Сопоставление товаров и услуг
├── reconciliation.py        # Периодическая сверка
├── models.py                # Pydantic-модели
├── logger.py                # structlog
├── exceptions.py            # Кастомные исключения
└── tests/
    ├── test_field_mappers.py
    ├── test_counterparty.py
    ├── test_order_processor.py
    └── fixtures/
        └── sample_order.json   # Структура на основе заказа #15674
```

### 4.3 Файл .env

```env
# WooCommerce
WC_URL=https://shop.example.com
WC_CONSUMER_KEY=ck_xxxx
WC_CONSUMER_SECRET=cs_xxxx
WC_WEBHOOK_SECRET=webhook_secret_xxxx

# Мой Склад
MS_LOGIN=admin@company
MS_PASSWORD=xxxx
# MS_TOKEN=xxxx

# UUID сущностей МС — подтверждены из тестовых данных
MS_ORGANIZATION_ID=0e57dba7-c413-11ee-0a80-13fd002f63f6
MS_STORE_ID=0e5a2b05-c413-11ee-0a80-13fd002f63f9
MS_CURRENCY_RUB_ID=0e5aa71e-c413-11ee-0a80-13fd002f63fe
MS_SALES_CHANNEL_ID=7bd2b1cd-e3b2-11ef-0a80-0ea7001f9cef
MS_STATE_NEW_LEAD_ID=22c4c846-13c8-11f1-0a80-0eb2000cd725

# UUID справочников (customentity) — получить элементы через setup_ms_ids.py
MS_CUSTOMENTITY_DELIVERY_SD_ID=1002cab7-2aa1-11f1-0a80-023a00303f73
MS_CUSTOMENTITY_DELIVERY_TYPE_ID=9a5d76d0-2aa1-11f1-0a80-023a003048c5
MS_CUSTOMENTITY_PAYMENT_TYPE_ID=f20d738a-2aa3-11f1-0a80-16d100307119

# UUID доп. полей заказа покупателя — все подтверждены из тестовых данных
MS_ATTR_ORDER_NUMBER_ID=70c4735f-c542-11f0-0a80-1755000e25a7
MS_ATTR_PAYMENT_METHOD_ID=33735877-ba29-11f0-0a80-1737003bc63e
MS_ATTR_PROMO_CODE_ID=ffcaeea9-13d2-11f1-0a80-0fd5000f56c4
MS_ATTR_DELIVERY_SD_ID=f887a45c-2aa2-11f1-0a80-1bd0003117b4
MS_ATTR_DELIVERY_TYPE_ID=10e587ca-2aa3-11f1-0a80-0f48002ee65f
MS_ATTR_PVZ_CODE_ID=308100c4-2aa3-11f1-0a80-01a9002fcf77
MS_ATTR_DELIVERY_COST_ID=04fee4e9-2aa5-11f1-0a80-0d860032db59
MS_ATTR_ESTIMATED_COST_ID=04fee891-2aa5-11f1-0a80-0d860032db5a
MS_ATTR_TOTAL_TO_PAY_ID=c5f954c4-2aa3-11f1-0a80-138c003062fd
MS_ATTR_PAYMENT_TYPE_ID=cbe57ab4-2aa4-11f1-0a80-1a29003029a1
MS_ATTR_COURIER_COMMENT_ID=d787efae-2aa4-11f1-0a80-145f0031ba34

# UUID элементов справочника "Прием платежа" — получить через setup_ms_ids.py
# Пример из тестовых данных: "1. Заказ предоплачен" = ff9acb1e-2aa3-11f1-0a80-1d3c00312319
MS_PAYMENT_TYPE_PREPAID_ID=ff9acb1e-2aa3-11f1-0a80-1d3c00312319
MS_PAYMENT_TYPE_NONCASH_ID=uuid-уточнить  # "2. Безналичная оплата"

# UUID элементов справочников "Доставка" и "Вид доставки" — получить через setup_ms_ids.py
# Пример из тестовых данных: "10. СДЭК" = 643a747a-2aa1-11f1-0a80-084b002f0a2c
# Пример из тестовых данных: "1 - Пункт выдачи" = e89a509e-2aa1-11f1-0a80-1bd00031011a

# Настройки
MS_MAX_REQUESTS_PER_SECOND=3
HOST=0.0.0.0
PORT=8000
LOG_LEVEL=INFO
```

### 4.4 Обработка ошибок

```
CRITICAL — заказ не создаётся:
  - Нет связи с API МС
  - Не удалось найти/создать контрагента
  - Не найдена организация или склад (константы)

ERROR — поле не заполнено, заказ создан:
  - Товар не найден по артикулу
  - Ошибка маппинга любого доп. поля

WARNING — некритично:
  - Телефон не нормализован
  - Несколько контрагентов по телефону
  - Неизвестный способ оплаты
  - Услуга доставки не найдена (создаём новую)
```

**Rate limit:** не более 3 запросов/сек (по умолчанию) — оставляет ~80% лимита МС другим интеграциям.

**Retry для запросов к МС:**
- HTTP 429 → ждать `Retry-After`, повторить
- HTTP 5xx → backoff 1/2/4 сек, max 3 попытки
- HTTP 4xx (кроме 429) → не ретраить, логировать ERROR с телом ответа
- POST/PUT при таймауте → **не ретраить** (сервер мог уже обработать запрос)
- Таймаут GET: 30 сек, POST/PUT: 60 сек

### 4.5 Статус заказа и принцип «только создание»

- При создании: всегда ставить `state` = "Новый лид", UUID = `22c4c846-13c8-11f1-0a80-0eb2000cd725`
- При `order.updated` + `status == "processing"`: **только проставить оплату** (создать входящий платёж `paymentin`). Никакие другие поля заказа не обновляются.
- При `order.updated` + способ оплаты «При получении» или «На карту» — **игнорировать**. Деньги ещё не получены (COD) или оплата ручная — менеджер проставляет сам.
- При `order.updated` с любым другим статусом (не `processing`) — **игнорировать**.
- При сверке (reconciliation): если заказ уже есть в МС — пропустить, не трогать
- **Интеграция никогда не обновляет и не удаляет существующие заказы в МС.** Менеджеры могут свободно вносить изменения вручную.

**Справочник статусов заказа в МС (полный список из тестовых данных):**

| UUID | Название | Тип |
|---|---|---|
| `22c4c846-13c8-11f1-0a80-0eb2000cd725` | Новый лид | Regular |
| `d05478db-18b7-11f1-0a80-1cac0000582d` | Новый лид 1 | Regular |
| `a4ea9a84-1d40-11f1-0a80-1d630057950d` | Новый лид 2 | Regular |
| `22c4d313-13c8-11f1-0a80-0eb2000cd726` | Взят в работу | Regular |
| `0f061f90-c413-11ee-0a80-13fd002f64a4` | В работе | Regular |
| `0f062083-c413-11ee-0a80-13fd002f64a5` | Первичка | Regular |
| `0f0621f2-c413-11ee-0a80-13fd002f64a8` | Завершён | Successful |
| `0f06229d-c413-11ee-0a80-13fd002f64aa` | Отменен | Unsuccessful |
| `0f062246-c413-11ee-0a80-13fd002f64a9` | Возврат | Unsuccessful |

> Интеграция использует только статус "Новый лид". Остальные статусы изменяются вручную менеджерами и интеграцией не затрагиваются.

### 4.6 Проставление оплаты (mark_paid)

**Когда вызывается:** вебхук `order.updated` + `status == "processing"` (только онлайн-оплата), либо сверка при `status == "processing"` или `"completed"`. Способы «При получении» и «На карту» — не проставляются автоматически.

**Алгоритм:**
1. Найти заказ(ы) в МС по доп. полю "Номер заказа на сайте" = `order_id` (и `order_id_1` для смешанных). Для основного заказа — до 3 ретраев (2/4/6 сек) на случай гонки с `order.created`.
2. Если `payedSum > 0` — платёж уже есть, пропустить.
3. Для каждого найденного заказа создать входящий платёж:
   ```
   POST /entity/paymentin
   {
     "organization": meta организации,
     "agent": {"meta": meta контрагента из заказа},   ← только meta, не полный объект
     "sum": сумма заказа (из поля sum, в копейках),
     "applicable": true,                               ← сразу проводить платёж
     "moment": "YYYY-MM-DD HH:mm:ss",                 ← из date_paid WC, если есть
     "operations": [{"meta": meta заказа, "linkedSum": сумма заказа}]
   }
   ```
4. Никакие другие поля заказа не трогаются.

**Таблица: когда оплата проставляется автоматически:**

| Способ оплаты | Статус WC | Через вебхук | Через сверку |
|---|---|---|---|
| Онлайн (эквайринг) | `processing` | ✅ | ✅ |
| Онлайн (эквайринг) | `completed` | ❌ | ✅ |
| На карту (ручная) | любой | ❌ | ❌ |
| При получении | любой | ❌ | ❌ |

### 4.7 Дедупликация и последовательная обработка вебхуков

**Дедупликация:**
- Ключ: `(order_id, action)`, где action = `"order.created"` / `"mark_paid"`
- TTL: 5 минут — повторный вебхук с тем же ключом игнорируется
- При ошибке обработки запись удаляется из дедупа → WC сможет повторить

**Последовательная обработка:**
- `asyncio.Lock` — вебхуки обрабатываются строго по одному
- Гарантирует, что пачка вебхуков не создаст параллельную нагрузку на МС

### 4.8 Периодическая сверка

Раз в 20 минут (окно проверки — 40 минут с перекрытием):
1. Получить все заказы WC за последние 40 минут: `GET /orders?after={iso}&before={iso}&per_page=100`, пагинация
2. Для каждого: проверка наличия в МС по `attributes.{MS_ATTR_ORDER_NUMBER_ID}={order_id}`
3. Не найден → `order_processor.process_order()` (создаёт новый)
4. Найден + `payedSum == 0` + статус `processing` или `completed` + способ оплаты не «При получении» и не «На карту» → `mark_paid`
5. Найден, остальные случаи → пропустить (**никогда не обновлять**)
6. Лог: `"Сверка: проверено={N}, в МС={M}, создано={K}, оплачено={P}, ошибок={E}"`

---

## 5. Процесс обработки заказа (flow)

```
1. Получить webhook payload
2. Верифицировать X-WC-Webhook-Signature (HMAC-SHA256)
   → Несовпадение: 401, WARNING

3. Распарсить JSON, определить топик (order.created / order.updated)

4. Дедупликация: ключ (order_id, action), TTL 5 мин
   → Дубликат: 200 {"status": "ignored", "reason": "duplicate"}

5. asyncio.Lock — последовательная обработка

6. Определить действие:
   - order.updated + status == "processing" + НЕ "при получении" и НЕ "на карту" → mark_paid (см. 6а)
   - order.updated + status == "processing" + "при получении" или "на карту" → игнорировать
   - order.updated + любой другой статус → игнорировать
   - order.created → создать заказ (см. 6б)

6а. mark_paid:
   - Найти заказ(ы) в МС по "Номер заказа на сайте" (основной + суффикс _1)
   - Для каждого: проверить payedSum == 0; если > 0 — пропустить
   - POST /entity/paymentin (organization, agent meta, sum, applicable=true, moment из date_paid, operations с linkedSum)
   - Не трогать другие поля заказа
   → Вернуть 200

6б. Создание заказа:
   - Проверка дубликата по доп. полю "Номер заказа на сайте" = order_data["id"]
     Найден → INFO, вернуть 200, выйти (никогда не обновлять)
   - Контрагент: counterparty_handler.find_or_create(billing)
     Ошибка → CRITICAL, вернуть 500
   - Маппинг полей (каждое в try/except; ошибка → WARNING, значение=None):
     тип доставки, строка адреса, код ПВЗ (regex [A-Z]+\d+),
     приём платежа, стоимости, способ оплаты, комментарий
   - Позиции: product_matcher.build_positions (regular / opened / services)
   - Сборка тела: organization, agent, store, state (Новый лид), salesChannel, rate,
     description, shipmentAddress, positions, attributes
   - POST /entity/customerorder
     Смешанный заказ (regular + opened) → 2 POST (основной + _1)
   → Вернуть 200

7. Ошибка → убрать из дедупа (WC сможет повторить), вернуть 500
   (500 только при CRITICAL — WC повторит попытку; не допускать накопления ошибок)
```

---

## 6. Открытые вопросы

| # | Вопрос | Статус |
|---|---|---|
| 1 | UUID всех доп. полей заказа | ✅ Все подтверждены из тестовых данных |
| 2 | UUID статуса "Новый лид" в МС | ✅ `22c4c846-13c8-11f1-0a80-0eb2000cd725` |
| 3 | UUID организации, склада, валюты, канала продаж | ✅ Все подтверждены из тестовых данных |
| 4 | UUID элемента "1. Заказ предоплачен" справочника "Прием платежа" | ✅ `ff9acb1e-2aa3-11f1-0a80-1d3c00312319` |
| 5 | UUID элемента "2. Безналичная оплата" справочника "Прием платежа" | ⬜ Получить через `GET /entity/customentity/f20d738a-2aa3-11f1-0a80-16d100307119` |
| 6 | UUID всех элементов справочника "Доставка" (СД) | ⬜ Получить через `GET /entity/customentity/1002cab7-2aa1-11f1-0a80-023a00303f73` |
| 7 | UUID всех элементов справочника "Вид доставки" | ⬜ Получить через `GET /entity/customentity/9a5d76d0-2aa1-11f1-0a80-023a003048c5` |
| 8 | Полный список значений `method_title` в WC (все способы доставки) | ⬜ Из реальных заказов |
| 9 | Полный список `payment_method_title` в WC | ⬜ Подтверждены: "При получении", "На карту". Остальные — уточнить |
| 10 | При `order.updated` — обновлять все поля или только определённые? | ✅ Только `mark_paid` при status=processing; остальные поля не обновлять |
| 11 | Когда начинать передавать промокод? | ⬜ Отложено |

---

## 7. Задачи для кодинга (промпты)

---

### Задача 0: Получение UUID из Мой Склад

**Промпт:**
```
Напиши Python-скрипт setup_ms_ids.py, который:

1. Подключается к API Мой Склад (логин/пароль из .env, библиотека requests)
2. Получает UUID следующих сущностей и выводит в формате для .env:
   - Организация "ИП Перфилов" → MS_ORGANIZATION_ID  (GET /entity/organization)
   - Склад "Sunscrypt основной" → MS_STORE_ID  (GET /entity/store)
   - Валюта RUB → MS_CURRENCY_RUB_ID  (GET /entity/currency)
   - Канал продаж "магазин" → MS_SALES_CHANNEL_ID  (GET /entity/saleschannel)
3. Получает метаданные заказа: GET /entity/customerorder/metadata
   - Выводит все статусы (name + id) — ищем "Новый лид" → MS_STATE_NEW_LEAD_ID
   - Выводит все доп. поля (name + id + type)
4. Для доп. полей типа "справочник" получает все элементы (name + id):
   - "Приём платежа"
5. Вывод в stdout: готовые строки .env

Каждый запрос в try/except с понятным сообщением.
Каждый блок прокомментирован на русском.
Задержка 0.1 сек между запросами.
Использовать: requests, python-dotenv.
```

---

### Задача 1: Конфигурация и логирование

**Промпт:**
```
Напиши модули config.py и logger.py для интеграции WooCommerce → Мой Склад.

config.py:
- Читает переменные из .env через python-dotenv
- Dataclass Config со всеми параметрами:
  WC_URL, WC_CONSUMER_KEY, WC_CONSUMER_SECRET, WC_WEBHOOK_SECRET,
  MS_LOGIN, MS_PASSWORD, MS_TOKEN (опционально),
  MS_ORGANIZATION_ID, MS_STORE_ID, MS_CURRENCY_RUB_ID,
  MS_SALES_CHANNEL_ID, MS_STATE_NEW_LEAD_ID,
  MS_ATTR_ORDER_NUMBER_ID, MS_ATTR_PAYMENT_METHOD_ID, MS_ATTR_PROMO_CODE_ID,
  MS_ATTR_PAYMENT_TYPE_ID, MS_ATTR_DELIVERY_COST_ID,
  MS_ATTR_TOTAL_TO_PAY_ID, MS_ATTR_PVZ_CODE_ID,
  MS_MAX_REQUESTS_PER_SECOND (int, default 3),
  HOST, PORT, LOG_LEVEL
- Обязательные (WC/МС ключи, organization, store): ValueError при отсутствии
- UUID доп. полей — могут быть None (WARNING при отсутствии)

logger.py:
- structlog (fallback: logging)
- timestamp + level + message + контекст
- stdout + файл (ротация 10 МБ, 5 файлов)
- уровень из config.LOG_LEVEL

Создать .env.example с описанием каждой переменной.
Каждый блок прокомментирован на русском.
```

---

### Задача 2: Клиент Мой Склад

**Промпт:**
```
Напиши модуль ms_client.py — HTTP-клиент для API Мой Склад.

Класс MoySkladClient:
- __init__: принимает config
  Basic Auth (MS_LOGIN:MS_PASSWORD) или Bearer (MS_TOKEN)
  Базовый URL: https://api.moysklad.ru/api/remap/1.2/

Методы get(path, params), post(path, data), put(path, data):
- Rate limiter: не более MS_MAX_REQUESTS_PER_SECOND запросов/сек
- Retry: 429 → Retry-After; 5xx → backoff 1/2/4 сек max 3 раза;
  4xx → выбросить исключение с телом ответа
- Логировать: метод, путь, статус, время ответа

Хелпер-методы:
- make_meta(entity_type: str, uuid: str) → dict
- find_by_filter(entity_type: str, filter_str: str) → list

Каждый блок прокомментирован на русском. Без asyncio.
```

---

### Задача 3: Обработчик контрагентов

**Промпт:**
```
Напиши модуль counterparty_handler.py.

Функция split_full_name(full_name: str) → tuple[str, str, str]:
  Разбивает ФИО на (firstName, lastName, middleName):
  1 слово → (parts[0], "", "")
  2 слова → (parts[0], parts[1], "")
  3+ слова → (parts[0], parts[1], " ".join(parts[2:]))
  name (поле МС) = исходная строка

Функция normalize_phone(phone: str) → str:
  Убирает лишние символы; 8... → +7...; 7... → +7...
  Результат не "+7..." длиной 12 → вернуть исходный, WARNING

Класс CounterpartyHandler:
- __init__: принимает ms_client
- find_or_create(billing: dict) → dict (meta):
  billing = {first_name, email, phone}
  Алгоритм: нормализовать телефон → поиск → найден/создать
  Подробно: раздел 3.2 документации.
  Ошибка → CRITICAL, CounterpartyError

Каждый блок прокомментирован на русском.
```

---

### Задача 4: Маппинг полей

**Промпт:**
```
Напиши модуль field_mappers.py.

Все функции логируют WARNING при неизвестных значениях.

1. detect_delivery_type(method_title: str) → str
   "pvz" если содержит "Самовывоз" или "постамат" (case-insensitive)
   "courier" — всё остальное

2. build_shipment_address(order_data: dict) → str
   pvz:     "Россия, " + shipping.address_2 + ", " + shipping.postcode
   courier: "Россия, " + shipping.city + ", " + shipping.address_1 + ", " + shipping.postcode
   Тип из detect_delivery_type(shipping_lines[0].method_title)

3. extract_pvz_code(order_data: dict) → str | None
   Только при pvz. Regex `[A-Z]+\d+` в shipping.address_2.
   "MSK2425, Москва, ул..." → "MSK2425"; "Москва, SBP892, ул..." → "SBP892"

4. map_payment_type(payment_method_title: str) → str | None
   lower() "на карту" или "онлайн" → "Заказ предоплачен"
   lower() "при получении" → "Безналичная оплата"
   иначе → WARNING, None

5. calculate_total_to_pay(order_data: dict) → float
   "при получении" in lower(payment_method_title) → float(total)
   иначе → 0.0

6. calculate_delivery_cost(order_data: dict) → float
   Если "на карту" in lower(payment_method_title) → 0.0
   Иначе → float(shipping_total)
   Это значение используется и для доп. поля "Стоимость доставки",
   и передаётся в product_matcher для расчёта цен позиций-услуг.

7. calculate_estimated_cost(order_data: dict) → str:
   Сумма всех line_items[].total, вернуть как строку.
   Пример: line_items с total 6831, 13491, 4491 → "24813"

8. extract_courier_comment(order_data: dict) → str | None:
   Вернуть customer_note. None если пустая строка.
   (Передаётся и в description, и в доп. поле "Комментарий курьеру")

9. extract_promo_code(order_data: dict) → str | None
   coupon_codes[0] или None. Реализовать, но пока НЕ вызывать.

10. build_attribute(attr_uuid: str, value, ms_base_url: str) → dict | None
   Структура доп. поля для МС. None если value или attr_uuid is None.
   Числовые значения приводить к строке перед передачей (тип string в МС).

Написать test_field_mappers.py для всех функций.
Тестовые данные на основе заказа #15674.
Каждый блок прокомментирован на русском.
```

---

### Задача 5: Сопоставление товаров и услуг

**Промпт:**
```
Напиши модуль product_matcher.py.

Класс ProductMatcher:
- __init__: принимает ms_client
- _product_cache: dict (sku → meta)
- _service_cache: dict (name → meta)

find_product(sku: str) → dict | None:
  1. Кэш
  2. GET /entity/product?filter=article={sku}
  3. Не найден → GET /entity/product?filter=externalCode={sku}
  4. Найден → кэш + вернуть meta; не найден → ERROR, None

find_or_create_service(name: str, price_rub: float) → dict | None:
  1. Кэш
  2. GET /entity/service?filter=name={name}
  3. Не найден → POST /entity/service {name: name}
     WARNING "Создана услуга: {name}"
  4. Вернуть meta. Ошибка → ERROR, None

build_positions(line_items: list, shipping_lines: list, is_card_payment: bool) → list[dict]:
  Товары: find_product(sku), price = int(float(price) * 100)
  Услуги: find_or_create_service(method_title),
          price = 0 если is_card_payment=True, иначе int(float(total) * 100), quantity=1
  None-позиции пропускать.

is_card_payment определяется в order_processor: True если payment_method_title
содержит "на карту" (без учёта регистра). "Онлайн оплата" и "При получении" — полная стоимость.

ВАЖНО: WC price — число; МС price — int в копейках (× 100).
Каждый блок прокомментирован на русском.
```

---

### Задача 6: Основной обработчик заказа

**Промпт:**
```
Напиши модуль order_processor.py.

Класс OrderProcessor:
- __init__: принимает config, ms_client, counterparty_handler, product_matcher
- _payment_type_cache: dict

Метод _get_payment_type_meta(name: str) → dict | None:
  GET /entity/customerorder/metadata → атрибут "Приём платежа"
  → customEntityId → GET /entity/customentity/{id}?filter=name={name}
  Результат кэшировать.

Метод process_order(order_data: dict, topic: str = "order.created") → list[dict]:

  1. order_id = str(order_data["id"])
     INFO "Обработка WC #{order_id}, топик={topic}"

  2. Поиск дубликата:
     GET /entity/customerorder?filter=attributes.{MS_ATTR_ORDER_NUMBER_ID}={order_id}
     - Найден + order.created → INFO, вернуть существующий (не обновлять!)
     - Не найден → POST

  3. Контрагент: counterparty_handler.find_or_create(billing)
     Ошибка → CRITICAL, OrderProcessingError

  4. Маппинг (каждое в try/except, ошибка → WARNING, None):
     shipment_address, pvz_code (regex [A-Z]+\d+),
     payment_type_key, delivery_sd_key, delivery_type_key,
     courier_comment, description (customer_note), payment_method_str

  5. Позиции: product_matcher.build_positions(line_items, shipping_lines, is_card_payment)
     Возвращает {"regular": [...], "opened": [...], "services": [...]}
     Смешанный заказ (regular + opened) → 2 заказа в МС

  6. Attributes (build_attribute; None не включать):
     order_number, payment_method, payment_type,
     delivery_sd, delivery_type, pvz_code,
     delivery_cost, estimated_cost, total_to_pay,
     courier_comment

  7. Тело запроса:
     Обязательные: organization, agent, store, rate, salesChannel
     state: "Новый лид" (ВСЕГДА при создании)
     Опциональные: description, shipmentAddress, positions, attributes

  8. POST /entity/customerorder
     Смешанный заказ → 2 POST: основной (order_id) + доп. (order_id_1)
     Успех → INFO "WC #{order_id} → МС #{ms_name}"

Метод mark_paid(order_data: dict) → list[dict]:
  Проставление оплаты. Вызывается при order.updated + status=="processing".
  1. Найти заказ(ы) в МС по номеру (основной + _1)
  2. Для каждого: POST /entity/paymentin {organization, agent, sum, operations}
  3. Не трогать другие поля заказа

ВАЖНО: интеграция никогда не обновляет и не удаляет заказы в МС.

Каждый блок прокомментирован на русском.
```

---

### Задача 7: FastAPI сервер и вебхук

**Промпт:**
```
Напиши main.py — FastAPI приложение.

lifespan: инициализировать все компоненты, запустить
reconciliation.schedule() (каждые 20 мин).

POST /webhook/order:
  1. Верифицировать X-WC-Webhook-Signature (HMAC-SHA256)
     Несовпадение → 401
  2. Топик из X-WC-Webhook-Topic
     Неизвестный → 200 (игнорировать)
  3. Дедупликация: ключ (order_id, action), TTL 5 мин
     Дубликат → 200 {"status": "ignored", "reason": "duplicate"}
  4. asyncio.Lock — последовательная обработка
  5. order.updated + status=="processing" → order_processor.mark_paid()
     order.created → order_processor.process_order()
  6. OrderProcessingError → убрать из дедупа, 500
     Другие исключения → убрать из дедупа, 500

ВАЖНО: 500 только когда заказ не создан. WC отключает вебхук после 5 подряд 500-х.

GET /health: {"status": "ok", "version": "2.0"}

Каждый блок прокомментирован на русском.
Использовать: fastapi, uvicorn, hmac, hashlib, base64, asyncio, time.
```

---

### Задача 8: Периодическая сверка

**Промпт:**
```
Напиши модуль reconciliation.py.

Класс Reconciliation:
- __init__: принимает config, woo_client, ms_client, order_processor

run():
  window: (now - 40 мин, now) — с перекрытием между запусками
  GET /orders?after=...&before=...&per_page=100, пагинация
  Для каждого: _order_exists_in_ms(order_id) — проверка наличия по доп. полю
  Найден → found++ (НЕ обновлять!); не найден → process_order(topic="order.created"), created++
  ошибка → errors++
  INFO итог.

schedule(interval_seconds=1200):  # 20 минут
  threading.Timer (daemon).
  Ошибка в run() → ERROR, планировщик продолжает.

ВАЖНО: сверка ТОЛЬКО проверяет наличие и создаёт недостающие.
Никогда не обновляет существующие заказы.

Каждый блок прокомментирован на русском.
```

---

### Задача 9: Тесты

**Промпт:**
```
Напиши тесты для интеграции WooCommerce → Мой Склад.

1. tests/fixtures/sample_order.json
   На основе заказа #15674 (полная структура, см. Приложение документации).

2. tests/test_field_mappers.py: тесты всех функций field_mappers.py
3. tests/test_counterparty.py (mock ms_client): все сценарии поиска/создания
4. tests/test_order_processor.py (mock): создание, дубликат, отсутствие товара,
   ошибка контрагента

Использовать pytest, unittest.mock.
Каждый тест с комментарием — что проверяем.
```

---

### Задача 10: Деплой

**Промпт:**
```
Напиши конфигурацию деплоя на Ubuntu 22+.

1. requirements.txt с зафиксированными версиями:
   fastapi, uvicorn[standard], requests, python-dotenv,
   structlog, APScheduler, woocommerce, pytest
2. woo-moysklad.service (systemd): uvicorn, EnvironmentFile=.env,
   Restart=on-failure
3. nginx.conf: HTTPS (certbot), proxy_pass 127.0.0.1:8000
4. deploy.sh: venv + pip install + systemctl restart
5. logrotate конфиг

Каждый файл прокомментирован на русском.
```

---

## 8. Порядок выполнения

```
Этап 1 — Подготовка:
  └─ Задача 0: setup_ms_ids.py → UUID в .env

Этап 2 — Инфраструктура:
  ├─ Задача 1: config.py + logger.py
  └─ Задача 2: ms_client.py

Этап 3 — Бизнес-логика:
  ├─ Задача 3: counterparty_handler.py
  ├─ Задача 4: field_mappers.py
  └─ Задача 5: product_matcher.py

Этап 4 — Интеграция:
  ├─ Задача 6: order_processor.py
  ├─ Задача 7: main.py
  └─ Задача 8: reconciliation.py

Этап 5 — Качество:
  └─ Задача 9: тесты

Этап 6 — Запуск:
  └─ Задача 10: деплой

Этап 7 — Валидация:
  └─ Прогон 5–10 тестовых заказов с ручной проверкой в МС
```

---

## Приложение: Эталонная структура заказа WC #15674

```json
{
  "id": 15674,
  "status": "processing",
  "total": "26188",
  "shipping_total": "1375",
  "payment_method_title": "При получении",
  "customer_note": "Promo:coinmetrica тест ТЕСТ",
  "billing": {
    "first_name": "Екатерина Кошенкова",
    "last_name": "",
    "email": "katerina@kosh.games",
    "phone": "+79099371845"
  },
  "shipping": {
    "address_1": "Пресненская набережная 10",
    "address_2": "MSK2425, Москва, ул. Садовая-Кудринская, 20",
    "city": "Москва",
    "postcode": "125464"
  },
  "shipping_lines": [
    {
      "method_title": "CDEK: Самовывоз (1 дней)",
      "total": "382"
    },
    {
      "method_title": "Наценка за наложенный платеж",
      "total": "993"
    }
  ],
  "line_items": [
    {"sku": "TG130X3-B",    "quantity": 1, "price": 6831},
    {"sku": "TG-RING",      "quantity": 1, "price": 13491},
    {"sku": "TG128X3-B-1",  "quantity": 1, "price": 4491}
  ],
  "coupon_codes": ["coinmetrica"]
}
```
