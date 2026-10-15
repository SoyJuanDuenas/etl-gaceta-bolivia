"""CLI del ETL de normas de la Gaceta Oficial de Bolivia.

Ejemplo:
    python main.py --start-date 2026-08-01 --end-date 2026-08-31
"""

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

from gaceta.etl import extract_normas
from gaceta.models import Norma
from gaceta.utils.logging_setup import setup_logging

# Nombre explícito (no __name__, que aquí sería "__main__") para que los logs
# del CLI se vean junto a los del paquete.
logger = logging.getLogger("gaceta.main")

DEFAULT_OUTPUT = Path("output.json")
STDOUT_MARKER = "-"  # convención Unix: --output - escribe en stdout


def _iso_date(valor: str) -> date:
    """Tipo de argparse: valida y convierte una fecha YYYY-MM-DD."""
    try:
        return date.fromisoformat(valor)
    except ValueError:
        raise argparse.ArgumentTypeError(f"fecha inválida {valor!r}, se espera YYYY-MM-DD") from None


def _positive_int(valor: str) -> int:
    """Tipo de argparse: entero >= 1 (para --limit)."""
    numero = int(valor)
    if numero < 1:
        raise argparse.ArgumentTypeError("--limit debe ser >= 1")
    return numero


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Lee y valida los argumentos de la línea de comandos."""
    parser = argparse.ArgumentParser(description="Extrae normas de la Gaceta Oficial de Bolivia a JSON.")
    parser.add_argument("--start-date", type=_iso_date, required=True, help="Inicio del rango (YYYY-MM-DD)")
    parser.add_argument("--end-date", type=_iso_date, required=True, help="Fin del rango (YYYY-MM-DD)")
    parser.add_argument("--limit", type=_positive_int, default=None, help="Máximo de normas a procesar")
    parser.add_argument(
        "--output", default=str(DEFAULT_OUTPUT),
        help=f"Ruta del JSON de salida (default: {DEFAULT_OUTPUT}); '{STDOUT_MARKER}' imprime en stdout",
    )
    args = parser.parse_args(argv)
    if args.end_date < args.start_date:
        parser.error("--end-date no puede ser anterior a --start-date")
    return args


def write_output(normas: list[Norma], output: str) -> None:
    """Escribe las normas como lista JSON en `output` (archivo o stdout)."""
    # ensure_ascii=False escribe los acentos tal cual ("Resolución") y no como escapes Unicode.
    contenido = json.dumps([norma.to_dict() for norma in normas], ensure_ascii=False, indent=2)
    if output == STDOUT_MARKER:
        print(contenido)
        return
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contenido + "\n", encoding="utf-8")
    logger.info("JSON escrito en %s", path.resolve())


def main(argv: list[str] | None = None) -> int:
    """Corre el ETL completo. Devuelve el exit code: 0 si todo salió bien, 1 si hubo
    errores o la corrida quedó incompleta (el JSON se escribe igual)."""
    # En Windows la consola puede usar cp1252 y corromper los acentos de la ayuda,
    # los logs y el JSON por stdout. Se fuerza UTF-8 antes de imprimir nada.
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    args = parse_args(argv)
    counter = setup_logging()
    logger.info("Inicio: rango %s a %s, limit=%s", args.start_date, args.end_date, args.limit)

    normas, stats = extract_normas(args.start_date, args.end_date, args.limit)
    # Se escribe siempre, incluso si hubo fallas: un resultado parcial vale más que nada.
    write_output(normas, args.output)

    logger.info(
        "Resumen: páginas=%d normas=%d descartadas=%d duplicadas=%d fecha_emision_nula=%d "
        "total_declarado=%s warnings=%d errores=%d%s",
        stats.paginas, stats.normas, stats.descartadas, stats.duplicadas, stats.fecha_emision_nula,
        stats.total_declarado, counter.counts["WARNING"], counter.counts["ERROR"],
        " (CORRIDA INCOMPLETA)" if stats.corrida_incompleta else "",
    )
    return 1 if counter.counts["ERROR"] or stats.corrida_incompleta else 0


if __name__ == "__main__":
    sys.exit(main())
