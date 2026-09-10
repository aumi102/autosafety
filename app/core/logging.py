import logging

import structlog

from app.core.config import get_settings


def setup_logging() -> None:
    settings = get_settings()
    logging.basicConfig(
        format="%(message)s",
        level=getattr(logging, settings.LOG_LEVEL),
    )
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

def get_logger(name: str) -> logging.Logger:
    logger: logging.Logger = structlog.get_logger(name)
    return logger
