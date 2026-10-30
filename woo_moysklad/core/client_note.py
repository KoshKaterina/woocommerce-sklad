# Данные клиента «как на сайте» для доп. поля заказа МС «Клиент с сайта»

from woo_moysklad.core.normalized_order import NormalizedOrder

# Предел текстового поля МС - 4096 символов; берём с запасом.
_MAX_LEN = 3500


def _one_line(value) -> str:
    return " ".join(str(value or "").split())


def build_site_client_note(order: NormalizedOrder) -> str | None:
    """Текст для менеджера: что клиент ввёл на сайте при оформлении.

    Контрагент и контакт amoCRM могут быть заведены раньше (звонок, чат) и хранить другое
    имя: интеграция реальное имя не перезаписывает. Этот текст остаётся в заказе как есть,
    а сервис amo_fix_fields кладёт его заметкой в сделку (поле читает он, формат менять
    только вместе с ним: строки вида «Имя: …», «Телефон: …»).

    None, если из заказа нечего показать (нет ни имени, ни телефона).
    """
    customer = order.customer
    name = _one_line(customer.full_name)
    phone = _one_line(customer.phone)
    if not name and not phone:
        return None

    lines = []
    for label, value in (
        ("Имя", name),
        ("Телефон", phone),
        ("Почта", _one_line(customer.email)),
        ("Телеграм", _one_line(customer.telegram)),
        ("Адрес доставки", _one_line(order.shipment_address)),
    ):
        if value:
            lines.append(f"{label}: {value}")

    comment = "\n".join(
        part for part in (_one_line(row) for row in str(order.description or "").splitlines()) if part
    )
    if comment:
        lines.append(f"Комментарий покупателя: {comment}")
    # Не ввод клиента, а сигнал менеджеру: заметку в сделку пишет amo_fix_fields, другого
    # пути в amo у предупреждения нет.
    if order.staff_warning:
        lines.append(_one_line(order.staff_warning))

    text = "\n".join(lines)
    return text[:_MAX_LEN]
