# Кастомные исключения интеграции WooCommerce → Мой Склад


class MoySkladAPIError(Exception):
    """Ошибка при обращении к API Мой Склад."""

    def __init__(self, message: str, status_code: int | None = None, response_body: str | None = None):
        self.status_code = status_code
        self.response_body = response_body
        super().__init__(message)


class MoySkladConnectionError(MoySkladAPIError):
    """МС недоступен по сети: обрыв, таймаут, не отвечает прокси.

    Не ошибка данных: тот же запрос стоит повторить позже. По этому признаку сверка понимает,
    что окно осталось непроверенным, и возвращается к нему (15.09.2026, заказ сайта 19046).
    """


class CounterpartyError(Exception):
    """Ошибка при поиске/создании контрагента. CRITICAL — заказ не может быть создан."""
    pass


class OrderProcessingError(Exception):
    """Ошибка обработки заказа. CRITICAL — заказ не создан."""
    pass
