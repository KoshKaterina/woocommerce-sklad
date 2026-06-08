# Хранилище state uCoz-поллера: last_processed_ucoz_order_id в JSON-файле.

import json
from pathlib import Path

from woo_moysklad.logger import get_logger

log = get_logger(__name__)


class UcozState:
    """Persistent-хранилище id последнего обработанного заказа uCoz.

    Формат файла:
        {"last_processed_ucoz_order_id": 391}

    Холодный старт: если файла нет — state пуст (last_id is None), поллер
    должен инициализироваться текущим id из API, не обрабатывая его.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(self) -> int | None:
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            log.error("uCoz state: не удалось прочитать", path=str(self.path), error=str(e))
            return None
        value = data.get("last_processed_ucoz_order_id")
        if not isinstance(value, int):
            log.warning("uCoz state: некорректное значение last_id", value=value)
            return None
        return value

    def save(self, last_id: int) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(
            json.dumps({"last_processed_ucoz_order_id": last_id}, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(self.path)
