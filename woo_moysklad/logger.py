# Настройка логирования: structlog → stdout + файл с ротацией

import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler

import structlog

_MSK_TZ = timezone(timedelta(hours=3))


def _add_msk_timestamp(logger, method_name, event_dict):
    event_dict["timestamp"] = datetime.now(_MSK_TZ).isoformat(timespec="seconds")
    return event_dict


def setup_logging(log_level: str = "INFO", log_file: str = "woo_moysklad.log"):
    """Инициализация structlog с выводом в stdout и файл с ротацией."""
    level = getattr(logging, log_level.upper(), logging.INFO)

    # Ротация: 10 МБ, максимум 5 файлов
    file_handler = RotatingFileHandler(
        log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setLevel(level)

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(level)

    # Формат для стандартного logging (используется structlog как обёртка)
    formatter = logging.Formatter("%(message)s")
    file_handler.setFormatter(formatter)
    stdout_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.handlers = []
    root_logger.addHandler(file_handler)
    root_logger.addHandler(stdout_handler)

    # Настройка structlog
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            _add_msk_timestamp,
            structlog.dev.ConsoleRenderer() if sys.stdout.isatty() else structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None):
    """Получить structlog логгер."""
    return structlog.get_logger(name)
