"""Parseo de fechas escritas en español ("28 DE AGOSTO DE 2026")."""

import re
from datetime import date

MESES = {
    "ENERO": 1,
    "FEBRERO": 2,
    "MARZO": 3,
    "ABRIL": 4,
    "MAYO": 5,
    "JUNIO": 6,
    "JULIO": 7,
    "AGOSTO": 8,
    "SEPTIEMBRE": 9,
    "SETIEMBRE": 9,  # variante usada en Bolivia
    "OCTUBRE": 10,
    "NOVIEMBRE": 11,
    "DICIEMBRE": 12,
}

# Fecha al inicio del texto. Admite ordinal en el día ("1RO", "1°", "1º").
FECHA_ESPANOL_RE = re.compile(
    r"^\s*(\d{1,2})(?:RO|°|º)?\s+DE\s+([A-ZÁÉÍÓÚÑ]+)\s+DE\s+(\d{4})",
    re.IGNORECASE,
)


def parse_fecha_espanol(texto: str) -> str | None:
    """Convierte la fecha en español al inicio de `texto` a ISO (YYYY-MM-DD).

    Devuelve None si no hay una fecha reconocible o si es inválida
    (mes desconocido, día fuera de rango, etc.). No loguea: el llamador
    conoce el contexto (document_id) y decide cómo reportarlo.
    """
    match = FECHA_ESPANOL_RE.match(texto)
    if not match:
        return None
    dia, nombre_mes, anio = match.groups()
    mes = MESES.get(nombre_mes.upper())
    if mes is None:
        return None
    try:
        return date(int(anio), mes, int(dia)).isoformat()
    except ValueError:
        return None
