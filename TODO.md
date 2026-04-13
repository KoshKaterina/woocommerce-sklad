# TODO

## 1. Интеграция с InSales

Добавить InSales как второй источник заказов наряду с WooCommerce.

### Подготовка данных (ручная)
- [ ] Проставить SKU для 17 вариантов без артикула в InSales (2-карточные и Family Pack новых цветов)
- [ ] Исправить SKU `TG-130X3-B` → `TG130X3-B` в InSales (или создать товар в МС)
- [ ] Создать в МС 15 товаров-коллабораций (Barter, Evans, Kaspa и т.д.) — если продаются
- [ ] Добавить организацию "ИП Абовян" UUID в .env (`MS_ORGANIZATION_INSALES_ID`)

### Код
- [ ] `insales_client.py` — HTTP-клиент InSales (Basic Auth, rate limit 500/5мин, пагинация updated_since+from_id)
- [ ] `insales_mapper.py` — маппинг полей InSales→МС:
  - ФИО: `client.name/surname/middlename` (уже разбито)
  - Телефон: `client.phone` (нормализация 8→+7 уже есть)
  - Оплата: `payment_title` → тот же `map_payment_type()`
  - Доставка СД: `delivery_info.shipping_company` (напрямую, без парсинга строк)
  - Вид доставки: `delivery_info.outlet.type` / `tariff_id`
  - Код ПВЗ: `delivery_info.outlet.external_id` → отрезать префикс "cdek#"
  - Адрес ПВЗ: `delivery_info.outlet.address` (готовый)
  - Адрес курьер: `shipping_address.city` + `shipping_address.address`
  - Цена товара: `order_lines[].sale_price` (финальная со скидкой)
  - Промокод: из `discounts[]` → в поле MS_ATTR_PROMO_CODE_ID
  - **Family Pack**: SKU `TG-FP{COLOR}` → 2 позиции (`TG128X3-B` + `TG-{COLOR}`), цена = sale_price/2 каждая
  - **Наценка COD**: 5% margin → отдельная услуга "Комиссия за наложенный платеж"
  - Организация: ИП Абовян (из конфига)
- [ ] Рефакторинг `order_processor.py` — универсальный, принимает замапленные данные
- [ ] Эндпоинт `/webhook/insales/order` (topics: orders/create, orders/update)
  - Нет HMAC — верификация через дозапрос заказа по API
- [ ] Маркировка оплаты InSales:
  - Проверять `paid_at` (не null) или `financial_status` = "paid"
  - Для COD — не маркировать
  - Отслеживать через polling + вебхук как ускоритель
- [ ] Reconciliation для InSales (polling updated_since каждые 3-5 мин):
  - Создание пропущенных заказов
  - Маркировка оплаты (по paid_at)
- [ ] Тесты

### Также исправить в WC-потоке
- [ ] Промокод: переделать из поля "комментарий" в поле "Промокод" (MS_ATTR_PROMO_CODE_ID), функция extract_promo_code() уже написана но не подключена

## 2. Обратная синхронизация полей из МС

При ручном изменении менеджером определённых полей заказа в МС — автоматически пересчитывать зависимые поля по тем же правилам, что и при создании заказа из WC.

- [ ] Polling каждые 3 минуты: запрос заказов, обновлённых за последний интервал
- [ ] Сравнение ключевых полей с ожидаемыми значениями
- [ ] Пересчёт зависимых полей по правилам из `field_mappers.py`
- [ ] Защита от зацикливания (игнорировать собственные обновления)
- [ ] Использовать общий async lock для исключения конфликтов с reconciliation
