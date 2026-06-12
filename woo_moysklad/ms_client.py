# HTTP-клиент для API Мой Склад с rate limiter и retry

import threading
import time

import requests

from woo_moysklad.exceptions import MoySkladAPIError
from woo_moysklad.logger import get_logger

log = get_logger(__name__)

MS_BASE_URL = "https://api.moysklad.ru/api/remap/1.2"


class MoySkladClient:
    """HTTP-клиент API Мой Склад с rate limiting и retry."""

    def __init__(self, config):
        self.config = config
        self.base_url = MS_BASE_URL
        self.session = requests.Session()

        # Авторизация: Bearer Token
        self.session.headers["Authorization"] = f"Bearer {config.MS_TOKEN}"

        self.session.headers["Content-Type"] = "application/json"
        self.session.headers["Accept-Encoding"] = "gzip"

        # Rate limiter: минимальный интервал между запросами
        self._min_interval = 1.0 / config.MS_MAX_REQUESTS_PER_SECOND
        self._last_request_time = 0.0

        # Потокобезопасность: requests.Session не thread-safe
        self._lock = threading.Lock()

        # Кэш meta-ссылок стран справочника МС ({name.lower(): meta|None})
        self._country_meta_cache: dict[str, dict | None] = {}

    def _rate_limit(self):
        """Ожидание для соблюдения лимита запросов."""
        now = time.monotonic()
        elapsed = now - self._last_request_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request_time = time.monotonic()

    def _request(self, method: str, path: str, params: dict | None = None,
                 json_data: dict | None = None, max_retries: int = 3) -> dict:
        """Выполнить HTTP-запрос с retry и rate limiting."""
        url = f"{self.base_url}/{path.lstrip('/')}"

        for attempt in range(max_retries):
            try:
                with self._lock:
                    self._rate_limit()
                    start = time.monotonic()
                    # POST/PUT — увеличенный таймаут, т.к. ретрай для них небезопасен
                    timeout = 60 if method in ("POST", "PUT") else 30
                    response = self.session.request(
                        method=method, url=url, params=params, json=json_data, timeout=timeout
                    )
                duration = round(time.monotonic() - start, 2)
                log.info("MS API", method=method, path=path, status=response.status_code, duration=duration)

                # HTTP 429 — превышен лимит, ждём Retry-After
                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", 3))
                    log.warning("MS API rate limit, ожидание", retry_after=retry_after)
                    time.sleep(retry_after)
                    continue

                # HTTP 5xx — ретрай с backoff
                if response.status_code >= 500:
                    if attempt < max_retries - 1:
                        backoff = 2 ** attempt  # 1, 2, 4 сек
                        log.warning("MS API 5xx, ретрай", status=response.status_code, backoff=backoff)
                        time.sleep(backoff)
                        continue
                    raise MoySkladAPIError(
                        f"MS API {response.status_code} после {max_retries} попыток",
                        status_code=response.status_code,
                        response_body=response.text[:500],
                    )

                # HTTP 4xx (кроме 429) — не ретраим
                if response.status_code >= 400:
                    raise MoySkladAPIError(
                        f"MS API {response.status_code}: {response.text[:500]}",
                        status_code=response.status_code,
                        response_body=response.text[:500],
                    )

                # Успех
                if response.status_code == 204:
                    return {}
                return response.json()

            except requests.RequestException as e:
                # POST/PUT не ретраим при таймаутах — сервер мог уже обработать запрос
                is_mutating = method in ("POST", "PUT")
                is_timeout = "timed out" in str(e).lower() or "timeout" in str(e).lower()
                if is_mutating and is_timeout:
                    log.error("MS API таймаут при POST/PUT, ретрай не безопасен", error=str(e), method=method, path=path)
                    raise MoySkladAPIError(f"Таймаут при {method} {path} (ретрай отключён): {e}")
                if attempt < max_retries - 1:
                    backoff = 2 ** attempt
                    log.warning("MS API ошибка соединения, ретрай", error=str(e), backoff=backoff)
                    time.sleep(backoff)
                    continue
                raise MoySkladAPIError(f"Ошибка соединения с МС: {e}")

        raise MoySkladAPIError("Исчерпаны попытки запроса к МС")

    def get(self, path: str, params: dict | None = None) -> dict:
        """GET-запрос к API МС."""
        return self._request("GET", path, params=params)

    def post(self, path: str, data: dict) -> dict:
        """POST-запрос к API МС."""
        return self._request("POST", path, json_data=data)

    def put(self, path: str, data: dict) -> dict:
        """PUT-запрос к API МС."""
        return self._request("PUT", path, json_data=data)

    def delete(self, path: str) -> dict:
        """DELETE-запрос к API МС (успех — HTTP 204, вернётся {})."""
        return self._request("DELETE", path)

    # --- Хелперы ---

    def make_meta(self, entity_type: str, uuid: str) -> dict:
        """Сформировать meta-ссылку на сущность МС."""
        return {
            "meta": {
                "href": f"{self.base_url}/entity/{entity_type}/{uuid}",
                "type": entity_type,
                "mediaType": "application/json",
            }
        }

    def make_custom_entity_meta(self, dictionary_id: str, element_id: str) -> dict:
        """Сформировать meta-ссылку на элемент справочника (customentity)."""
        return {
            "meta": {
                "href": f"{self.base_url}/entity/customentity/{dictionary_id}/{element_id}",
                "type": "customentity",
                "mediaType": "application/json",
            }
        }

    def find_country_meta(self, name: str) -> dict | None:
        """meta-ссылка на страну справочника МС по названию (с кэшем).

        Страну НЕ хардкодим (бывают зарубежные заказы). Не нашли — None,
        тогда country в shipmentAddressFull просто не пишется.
        """
        if not name:
            return None
        key = name.strip().lower()
        if key in self._country_meta_cache:
            return self._country_meta_cache[key]

        meta = None
        try:
            resp = self.get("entity/country", params={"filter": f"name={name}"})
            rows = resp.get("rows", [])
            if rows:
                meta = {"meta": rows[0]["meta"]}
        except Exception:
            meta = None  # справочник недоступен — пишем адрес без страны

        self._country_meta_cache[key] = meta
        return meta

    def make_state_meta(self, entity_type: str, state_uuid: str) -> dict:
        """Сформировать meta-ссылку на статус сущности МС.

        Генерирует /entity/{entity_type}/metadata/states/{state_uuid},
        а не /entity/state/{uuid} (которого не существует).
        """
        return {
            "meta": {
                "href": f"{self.base_url}/entity/{entity_type}/metadata/states/{state_uuid}",
                "type": "state",
                "mediaType": "application/json",
            }
        }

    def find_by_filter(self, entity_type: str, filter_str: str) -> list:
        """Поиск сущностей по фильтру. Возвращает список rows."""
        result = self.get(f"entity/{entity_type}", params={"filter": filter_str})
        return result.get("rows", [])
