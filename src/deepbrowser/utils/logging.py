import logging
from typing import Any


_default_root_logger = logging.getLogger()


def create_stream_logging_handler(
    log_level: int, root_logger: logging.Logger = _default_root_logger
) -> logging.StreamHandler[Any]:
    """
    Sets up logging with a single handler which emits logs to stderr.
    """
    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(log_level)

    root_logger.setLevel(log_level)
    root_logger.addHandler(stream_handler)

    return stream_handler
