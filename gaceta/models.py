"""Modelos del registro de salida."""

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Anexo:
    """Documento adicional anexado a la norma (distinto del PDF principal)."""

    descripcion: str | None
    url: str


@dataclass
class Norma:
    """Una norma publicada en la Gaceta Oficial de Bolivia.

    El orden de los campos es el orden de las claves en el JSON de salida.
    """

    document_id: str  # id de "Ver Norma"; clave de deduplicación
    titulo: str  # ej. "Decreto Supremo N° 5699"
    fecha_publicacion: str  # YYYY-MM-DD, de la cabecera de la tarjeta
    fecha_emision: str | None  # YYYY-MM-DD, del inicio del epígrafe; None si no es válida
    epigrafe: str  # resumen del objeto de la norma, sin el prefijo de fecha
    anexo_url: str  # enlace "Descargar PDF"
    edicion: str  # código de edición, ej. "2093NEC"
    entidad_emisora: str | None  # enlace "Clasificación"; None si la norma no la trae
    fuente_url: str  # vista de la norma (/normas/verGratis_gob/{id})
    anexos_adicionales: list[Anexo] = field(default_factory=list)  # [] si no hay

    def to_dict(self) -> dict[str, Any]:
        """Representación serializable a JSON (incluye los anexos anidados)."""
        return asdict(self)
