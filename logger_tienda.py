# logger_tienda.py
# Logger central para todos los scripts de la tienda Magic.

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from config_tienda import PROJECT_ROOT


# Carpeta y archivo de logs
LOG_DIR: Path = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

LOG_FILE: Path = LOG_DIR / "tienda_magic.log"


def _configure_root_logger() -> logging.Logger:
    """
    Configura el logger raíz 'tienda_magic' (solo una vez).
    """
    logger = logging.getLogger("tienda_magic")
    if logger.handlers:
        # Ya está configurado
        return logger

    logger.setLevel(logging.INFO)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Handler a archivo con rotación
    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=2 * 1024 * 1024,  # 2 MB por archivo
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    # Handler a consola (para cuando lo ejecutes manualmente)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)
    logger.addHandler(console_handler)

    return logger


def get_logger(name: str = "tienda_magic") -> logging.Logger:
    """
    Devuelve un logger hijo con nombre, p.ej. 'tienda_magic.auto_etiquetar'.
    """
    root = _configure_root_logger()
    if name == root.name:
        return root
    return root.getChild(name)


def log_info(msg: str, logger: logging.Logger | None = None) -> None:
    if logger is None:
        logger = get_logger()
    logger.info(msg)


def log_warning(msg: str, logger: logging.Logger | None = None) -> None:
    if logger is None:
        logger = get_logger()
    logger.warning(msg)


def log_error(msg: str, logger: logging.Logger | None = None) -> None:
    if logger is None:
        logger = get_logger()
    logger.error(msg)


def log_exception(exc: BaseException, logger: logging.Logger | None = None, msg: str | None = None) -> None:
    """
    Loguea una excepción con stack trace.
    """
    if logger is None:
        logger = get_logger()
    if msg is None:
        msg = f"Excepción no controlada: {exc!r}"
    logger.error(msg, exc_info=True)
