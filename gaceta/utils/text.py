"""Helpers de texto."""


def normalize_space(texto: str) -> str:
    """Colapsa espacios, tabs, saltos de línea y &nbsp; en un único espacio."""
    return " ".join(texto.split())
