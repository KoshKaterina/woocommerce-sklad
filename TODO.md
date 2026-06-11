# TODO

## 1. Интеграция с InSales

Добавить InSales как второй источник заказов наряду с WooCommerce.

### Подготовка данных (ручная)
- [x] SKU-маппинг InSales → МС захардкожен в `insales_normalizer._INSALES_SKU_TO_MS_SKUS` и `_INSALES_VARIANT_ID_TO_MS_SKUS` (для пустых SKU). При появлении новых товаров в InSales — обновлять оба словаря.
- [ ] Создать в МС 15 товаров-коллабораций (Barter, Evans, Kaspa и т.д.) — если продаются
- [x] Добавить организацию "ИП Абовян" UUID в .env (`MS_ORGANIZATION_INSALES_ID`)
- [x] Для самовывоза из офиса атрибут "Доставка (СД)" не заполняем — только услуга "Самовывоз из офиса Sunscrypt" в позициях заказа. `MS_DELIVERY_SD_PICKUP_ID` удалён из config/.env.

### Код
- [x] `insales_client.py` — HTTP-клиент InSales (Basic Auth, rate limit 500/5мин, пагинация updated_since+from_id)
- [x] `insales_normalizer.py` — маппинг полей InSales→NormalizedOrder:
  - ФИО, телефон, оплата, Доставка СД, Вид доставки, Код ПВЗ, адреса, цена, промокод
  - **Family Pack**: SKU `TG-FP{COLOR}` → 2 позиции (`TG128X3-B` + `TG-{COLOR}`), цена = sale_price/2 каждая
  - **Наценка COD** `margin_amount` → отдельная услуга "Наценка за наложенный платеж" (имя совпадает с WC shipping_line)
  - Организация: ИП Абовян (из конфига)
  - Самовывоз из Шоурума → `delivery_sd=None` (атрибут не ставим), имя услуги = "Самовывоз из офиса Sunscrypt"
  - **Промокод**: парсится из `discounts[i].description` по шаблону `"Скидка по купону <CODE>"` (InSales не кладёт код отдельным полем). Фикстура: `tests/fixtures/insales_order_with_promo.json`
  - **delivery_cost / total_to_pay** для атрибутов МС приводятся к целым рублям (`int(round(...))`) — совпадает с форматом WC
- [x] Рефакторинг `order_processor.py` — через `NormalizedOrder`, `process_normalized_order`
- [ ] Эндпоинт `/webhook/insales/order` (topics: orders/create, orders/update) — делаем в последнюю очередь
  - Нет HMAC — верификация через дозапрос заказа по API
- [x] Маркировка оплаты InSales (`mark_paid_insales` + адаптер `should_mark_paid`):
  - Правило: `paid_at is not None AND financial_status == "paid"`
  - COD не маркируется (правило покрывает)
  - Работает через reconciliation polling; задержка до 20 мин
- [x] Reconciliation унифицирован для WC и InSales через `SourceAdapter`:
  - `source_adapter.py`: `WooSourceAdapter`, `InSalesSourceAdapter`
  - Один обход на источник: для каждого заказа — либо `process` (если нет в МС), либо `mark_paid` (если надо и не оплачен)
  - Поля заказа НЕ сравниваются (менеджеры правят вручную)
  - WC фильтр: `modified_after`/`modified_before`; InSales: `updated_since` + клиентский фильтр по верхней границе
- [x] Тесты нормалайзера InSales (`test_insales_normalizer.py`)
- [x] Тесты reconciliation + адаптеров (`test_reconciliation.py`)
- [x] Тесты интеграции `process_insales_order` через `OrderProcessor` (`test_insales_e2e.py`, 5 кейсов на фикстурах: sample/COD/familypack/courier + skip-existing)

### Также исправить в WC-потоке
- [x] Промокод: в `wc_normalizer` выделен отдельно (через `extract_promo_code`), передаётся в MS_ATTR_PROMO_CODE_ID
- [x] Hotfix СДЭК перенесён в `wc_normalizer`: обнуляем `price_cents` услуги только при `card_payment AND is_cdek`

## 2. Интеграция с uCoz (TG-магазин) — в процессе

### Архитектура uCoz-потока (зафиксировано)

- **Источник API:** `https://sunscrypt.usite.pro/php/uamo.php` — отдаёт ТОЛЬКО последний заказ. Параметры (`?id=N` и пр.) игнорируются. Правка PHP-скрипта на стороне uCoz пока не делается (нет креды PHP FTP, секретный вопрос неизвестен).
- **Polling каждые 1 минуту** (отдельный `threading.Timer`, не через общий 20-минутный `Reconciliation`). Эндпоинт лёгкий (один JSON с одним заказом), 1440 запросов/сутки — пренебрежимая нагрузка.
- **Стейт** `last_processed_ucoz_order_id` хранится в `data/ucoz_state.json` (в корне проекта).
- **Холодный старт** (нет файла state) — берём текущий id из ответа как стартовую точку, ничего не обрабатываем (чтобы не засыпать МС старыми заказами).
- **Детекция gap'а** (`new_id > last_id + 1`) → пока просто `log.error` с перечислением пропущенных id. Когда появится общий механизм алертинга (telegram-уведомления для всех интеграций) — переключаем на TG.
- **Признак оплаты:** `inv_params.startswith("payment_accepted")` → mark_paid для онлайн-оплаты.
- **Суффикс номера заказа в МС** — постоянный ` TGShop` (по аналогии с ` Tangemshop` у InSales): защищает от коллизии числовых id WC/uCoz.

### Формат ответа uamo.php (пример сохранён в `tests/fixtures/ucoz_order_sample.json`)

```json
{
  "order": {
    "id": 391,                              // int, порядковый номер
    "add_date": 1776394083,                 // unix timestamp
    "order_hash": "...",
    "uid": 2559,
    "user": "andreia7287",                  // @username (без @) → email контрагента
    "id_tg": "231878353",                   // telegram user_id → не используем
    "payment_id": 3,                        // см. справочник (Q7: 6=крипта → prepaid+"Wallet")
    "delivery_id": 4,                       // см. справочник (Q: уточнить варианты)
    "inv_params": "payment_accepted#...",   // начинается с payment_accepted → оплачено
    "amount": "8990.00",                    // итог
    "delivery_tax": "0.00",                 // стоимость доставки
    "discount_sum": "0.00",
    "delivery_data": "{}",                  // JSON-строка (в ПВЗ содержит код/адрес)
    "payment_topay": "  ₽ 8990",
    "field_phone": "79588132822",
    "field_full_name": "Андрей",
    "field_email": "pelii@ya.ru",
    "field_delivery_address": "Реутов, Ашхабадская улица 19Б, к36",
    "field_order_comment": "Домофон: 50#329"
  },
  "goods": [
    {"goods_id": 134, "name": "Blockstream Jade Orange", "price_raw": 8990, "count": 1}
  ]
}
```

### Ответы по открытым вопросам

- [x] **Q5**: организация МС для uCoz = **ИП Перфилов** (MS_ORGANIZATION_ID — дефолт, без переопределения)
- [x] **Q6**: `@user` → в поле **email контрагента** (формат: `@{user}`). `id_tg` не используем.
- [x] **Q7**: крипта (`payment_id=6`) → атрибут "Приём платежа" = `prepaid` (Заказ предоплачен), атрибут "Способ оплаты" = строка `"Wallet"`. Новый UUID элемента справочника НЕ нужен.
- [x] **Q9**: трек СДЭК из uCoz не передаётся — игнорируем, работаем только с адресом.

### Данные и маппинги

- [x] **Экспорт товаров uCoz** в `docs/reference/ucoz_goods.csv` (242 позиции, windows-1251). Промежуточный черновик маппинга (`_ucoz_ms_match.csv`, `_ms_products.json`) — рабочие артефакты, удалены после сборки итогового словаря (gitignored).
- [x] **Проверка маппинга**: из 242 позиций 222 точных (score=1.0), 20 нечётких (score<1.0), 9 без МС-article (товар в МС без артикула — маппить по имени/UUID).
- [x] **Исключённые бренды** (не маппятся — не продаются больше) — учтены в словаре:
  - **Coinkite** целиком: Coldcard (MK3/MK4/MK5/Q все цвета), SATSCARD, TAPSIGNER, HARDCASE-чехол, кабели CoinKite, адаптер питания Coldcard, набор костей энтропии, Security Bag Kit. ucoz_id: 118, 119-128, 153, 178, 192, 193, 201, 204, 249-254, 1106-1114.
  - **SecuX** (v20/w20/w10/Nifty): ucoz_id 137-140.
  - **Satochip (Satodime)**: ucoz_id 260.
  - **Мусор**: ucoz_id 165 (Yubikey Security Key без уточнения, цена 4750).
- [x] Итоговый словарь `_UCOZ_GOODS_ID_TO_MS_SKU` в `ucoz_normalizer.py` — 204 позиции (242 минус 38 исключений).

### Код

- [x] `ucoz_client.py` — HTTP-клиент uamo.php (без авторизации, отдаёт только последний заказ).
- [x] `ucoz_state.py` — JSON-стейт `last_processed_ucoz_order_id`, защита от ротации id.
- [x] `ucoz_poller.py` — `threading.Timer` с интервалом 1 мин (`UCOZ_POLL_INTERVAL_SECONDS`), холодный старт, gap-детекция через `log.error`, защита от гонки (state не сохраняется при ошибке processor → retry).
- [x] `ucoz_normalizer.py` — `goods_id → SKU`, `payment_id → (title, type_key)`, `delivery_id → (service_name, sd_key, type_key)`, `@user → email`, `inv_params → is_paid`, суффикс ` TGShop` у номера заказа.
- [x] `OrderProcessor.process_ucoz_order` подключён к нормалайзеру.
- [x] Интеграция в `main.py/lifespan` (UCOZ_POLL_URL → запуск поллера, опционально).
- [x] Тесты: `test_ucoz.py` (client/state/poller), `test_ucoz_normalizer.py` (маппинг полей).

### Открытые вопросы по справочникам uCoz (для нормалайзера)

Без этих данных заказ всё равно создаётся, но атрибуты СД/Вид доставки и точное название способа оплаты не заполняются — менеджер дополняет вручную.

- [ ] **Способы оплаты** (`payment_id`): таблица `id → название` всех вариантов. Сейчас в `_UCOZ_PAYMENT_ID_TO_INFO` известны только: 3=Яндекс Касса (prepaid), 6=Wallet (prepaid).
- [ ] **Способы доставки** (`delivery_id`): таблица `id → название` + категория (СДЭК ПВЗ/курьер/постамат, самовывоз, Почта России, …). Сейчас `_UCOZ_DELIVERY_ID_TO_INFO` пуст: имя услуги дефолтится в "Доставка", СД/Вид доставки = None.
- [ ] **Формат `delivery_data`** (JSON-строка) для разных типов (ПВЗ, курьер, самовывоз): примеры. Пока используется только `field_delivery_address` как готовый адрес; код ПВЗ не извлекаем.

## 3. Общий механизм алертинга (отложено)

- [ ] TG-уведомления для всех интеграций (WC, InSales, uCoz): CRITICAL-ошибки создания заказов, пропуски заказов uCoz (gap в нумерации), ошибки маркировки оплаты
- [ ] Единый канал/бот, конфигурация через `.env` (`ALERT_TG_TOKEN`, `ALERT_TG_CHAT_ID`)
- [ ] После внедрения — в uCoz-интеграции переключить gap-детектор с `log.error` на TG

## 4. Обратная синхронизация полей из МС (реализовано, включено по умолчанию)

При ручном изменении менеджером заказа в МС — пересчитываем зависимые доп.поля по тем же правилам, что и при создании. Модуль `core/field_resync.py`, `FIELD_RESYNC_ENABLED=true` по умолчанию (отключить — `=false`).

- [x] Polling каждые 3 мин, окно 9 мин (в том же таймере, что reconciliation → без гонки)
- [x] Работает по ВСЕМ заказам МС в окне, КРОМЕ канала «Маркетплейс» (в т.ч. созданные менеджером вручную, без «Номера заказа на сайте»)
- [x] Пересчёт: Оценочная = Σ товаров, Стоимость доставки = Σ услуг, Итого = Σ всего при COD / 0 при предоплате, Прием платежа из «Способ оплаты»
- [x] «Способ оплаты»: case-insensitive, допуск опечаток; «на карту»/«перевод» → предоплата + обнуление СДЭК; «онлайн»/«картой» → предоплата без обнуления; «получ»/«налож» → COD (без «налич»); нераспознанное → пропуск + WARNING
- [x] Обнуление цены услуги СДЭК → 0 при «на карту»/«банковский перевод» (только СДЭК, как при создании)
- [x] Защита от зацикливания: пишем только дифф → нет правок → нет записи → `updated` не дёргается. «Изменён только контрагент» → записи нет
- [x] НЕ трогаем: контрагента, статус, адрес, вид доставки/СД, товары/услуги (кроме обнуления СДЭК)
- [x] Тесты `test_field_resync.py`; ручной тест `scripts/resync_order.py --order <N> --dry-run`
- [ ] Перед включением в проде — прогнать dry-run на нескольких реальных заказах
- [ ] Уточнить точность сумм: сейчас целые рубли (`//100`, как при создании); если нужны копейки — менять и создание тоже

## 5. Разнесение адреса доставки в shipmentAddressFull (WC, реализовано)

На сайт подключён плагин DaData (стандартизация курьерских адресов; обязательна на checkout). Кроме плоского `shipmentAddress` теперь заполняем нативный объект `shipmentAddressFull` (раскрывающийся редактор «Адрес доставки» в МС).

- [x] `core/address_parser.py`: `ShipmentAddressParts`, `parse_wc_address(shipping, delivery_type)`, `ISO_TO_COUNTRY_NAME`
- [x] Курьер: `postalCode`←`shipping.postcode`, `city`←`shipping.city`, `country`←`shipping.country` (ISO); улицу/дом/квартиру парсим из стандартизованной DaData-строки `shipping.address_1`
- [x] Парсер справа-налево: квартира (кв/офис/оф/помещ) → дом (д/двлд/дом + корп/стр, fallback на голый номер) → улица (остаток минус локалити: страна/обл/край/респ/р-н/администрация/«г »/город==shipping.city)
- [x] ПВЗ/постамат: best-effort из `shipping.address_2` (срезаем код ПВЗ и дубль города); самовывоз из офиса — адреса нет
- [x] `ms_client.find_country_meta(name)`: резолв страны в справочник МС по имени (ISO→название), кэш, graceful-None. Страну НЕ хардкодим — бывают KZ/BY
- [x] `order_processor._build_shipment_address_full`: пишем вместе с плоским `shipmentAddress`
- [x] Попутно: `build_shipment_address` больше не хардкодит «Россия» (баг для зарубежных) — страна по ISO с дефолтом «Россия»
- [x] Тесты `test_address_parser.py` на реальных образцах; проверено на тестовых заказах в МС
- [x] Доработки по аудиту 2026-06-11 (60 заказов нового кода):
  - [x] Код ПВЗ — из меты CDEK `_official_cdek_office_code` (первично), address_2 — fallback (бывает пуст: заказ 17130)
  - [x] Город ПВЗ — fallback из `_official_cdek_city`
  - [x] addInfo («Другое») не заполняем — `shipping.state` дублировал город в 57/60 заказов
  - [x] Самовывоз из офиса — адрес не пишем вообще (раньше уходил фиктивный «Россия, Москва»)
  - [x] Ретро-фикс созданных заказов: `scripts/fix_address_retro.py` (dry-run/--apply)
- [ ] **Сайт (не интеграция):** чекаут «Доставка курьером по Москве» не передаёт адрес вообще (17083, 17109) — DaData-поле не привязано к методу?
- [ ] **Регион** — справочник МС, в MVP не пишем. Включить — резолв имени региона в GUID справочника регионов МС с кэшем
- [ ] **InSales** — разнести адрес по тем же `parts` (пока заполняется только плоский адрес)
- [ ] Имя страны в справочнике МС должно быть ровно «Россия»/«Казахстан»/… (иначе `country` тихо опускается) — при необходимости расширить `ISO_TO_COUNTRY_NAME`
