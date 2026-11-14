import logging
import sys
from app.core.dev_logger import log_dev_api_call

__all__ = ["configure_logging", "log_dev_api_call"]


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )
