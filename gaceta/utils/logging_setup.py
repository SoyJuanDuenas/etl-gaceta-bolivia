"""Configuración de logging: consola + archivo por corrida."""

import logging
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"
# Un archivo por corrida: logs/etl_YYYYmmdd_HHMMSS.log (el más reciente es la última corrida).
LOG_DIR = Path("logs")


class LevelCounter(logging.Handler):
    """Cuenta los registros emitidos por nivel, para el resumen final."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.counts: Counter[str] = Counter()

    def emit(self, record: logging.LogRecord) -> None:
        self.counts[record.levelname] += 1


def setup_logging() -> LevelCounter:
    """Configura el logger raíz y devuelve el contador de registros por nivel.

    - Consola: INFO, para seguir la corrida (páginas, warnings, errores, resumen).
    - Archivo en LOG_DIR: siempre en DEBUG, para diagnosticar después sin volver a correr.
    - Contador: alimenta el resumen final y el exit code.
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(LOG_FORMAT)

    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.INFO)
    console.setFormatter(formatter)

    log_file = logging.FileHandler(LOG_DIR / f"etl_{datetime.now():%Y%m%d_%H%M%S}.log", encoding="utf-8")
    log_file.setLevel(logging.DEBUG)
    log_file.setFormatter(formatter)

    counter = LevelCounter()

    # El raíz deja pasar todo; cada handler filtra a su propio nivel.
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers = [console, log_file, counter]
    # urllib3 loguea cada conexión en DEBUG; no aporta para diagnosticar el ETL.
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    return counter
