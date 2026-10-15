"""Orquestación: recorre el listado, parsea cada norma y acumula estadísticas."""

import logging
from dataclasses import dataclass
from datetime import date

import requests
from bs4 import Tag

from gaceta.config import PAGINAS_SIN_NOVEDAD_MAX
from gaceta.crawler import HttpClient, iter_listing_pages
from gaceta.models import Norma
from gaceta.parser import ParseError, card_document_id, find_cards, parse_card, parse_resumen

logger = logging.getLogger(__name__)


@dataclass
class RunStats:
    """Contadores de una corrida, para el resumen final y el exit code."""

    paginas: int = 0  # páginas del listado descargadas (incluye las repetidas del final)
    normas: int = 0  # normas válidas y únicas que van al JSON
    descartadas: int = 0  # tarjetas que no se pudieron parsear
    duplicadas: int = 0  # document_id repetidos (se conserva la primera aparición)
    fecha_emision_nula: int = 0  # normas sin fecha de emisión reconocible
    total_declarado: int | None = None  # "N en total" según el encabezado del sitio
    corrida_incompleta: bool = False  # falló la red, o el resultado no es confiable (ver extract_normas)


def _parse_card_safely(card: Tag, page_url: str, posicion: int) -> Norma | None:
    """Una norma que falla se loguea con contexto y se descarta; el resto sigue."""
    try:
        return parse_card(card)
    # Falla esperada (falta un campo): basta el mensaje, sin traceback.
    except ParseError as exc:
        logger.error(
            "Norma descartada (document_id=%s, posición %d en %s): %s",
            card_document_id(card), posicion, page_url, exc,
        )
    # Except amplio a propósito, solo por norma: un HTML inesperado no debe
    # tumbar la corrida. logger.exception guarda el traceback para diagnosticarlo.
    except Exception:
        logger.exception(
            "Error inesperado parseando norma (document_id=%s, posición %d en %s)",
            card_document_id(card), posicion, page_url,
        )
    return None


def extract_normas(start: date, end: date, limit: int | None = None) -> tuple[list[Norma], RunStats]:
    """Extrae las normas del rango, sin duplicados por document_id.

    El listado termina cuando PAGINAS_SIN_NOVEDAD_MAX páginas seguidas no traen
    ningún document_id nuevo (una página fuera de rango repite la última).

    Devuelve lo extraído con `stats.corrida_incompleta = True`, en lugar de
    perderlo o de darlo por bueno, si: la red falla a mitad de la corrida, una
    página no trae ninguna tarjeta (señal de que cambió el HTML), o lo
    procesado no cuadra con el total que declara el sitio.
    """
    client = HttpClient()
    stats = RunStats()
    normas: list[Norma] = []
    vistos: set[str] = set()
    # Todos los document_id listados, también los de tarjetas descartadas: es
    # lo que decide si una página trae algo nuevo.
    listados: set[str] = set()
    paginas_sin_novedad = 0

    try:
        for page_url, soup in iter_listing_pages(client, start, end):
            stats.paginas += 1
            # El encabezado no decide la paginación: si no aparece, se sigue igual.
            resumen = parse_resumen(soup)
            if resumen and stats.total_declarado is None:
                stats.total_declarado = resumen.total_normas
            cards = find_cards(soup)

            # Sin tarjetas no hay forma de saber si el listado terminó: el fin
            # real es una página repetida, nunca una vacía. Solo un rango sin
            # normas ("0 en total") puede traer 0 tarjetas legítimamente.
            if not cards:
                if resumen and resumen.total_normas == 0:
                    logger.info("El rango no tiene normas (%s)", page_url)
                else:
                    stats.corrida_incompleta = True
                    logger.error(
                        "La página %s no trae ninguna norma; posible cambio en el HTML del sitio. Se detiene",
                        page_url,
                    )
                break

            ids = {card_document_id(card) for card in cards}
            if ids <= listados:
                paginas_sin_novedad += 1
                logger.info(
                    "Página sin normas nuevas (%d/%d): %s", paginas_sin_novedad, PAGINAS_SIN_NOVEDAD_MAX, page_url,
                )
                if paginas_sin_novedad >= PAGINAS_SIN_NOVEDAD_MAX:
                    break
                continue
            paginas_sin_novedad = 0
            listados |= ids

            logger.info(
                "Página %s de %s: %d normas (%s)",
                resumen.pagina if resumen else "?", resumen.paginas if resumen else "?",
                len(cards), page_url,
            )

            for posicion, card in enumerate(cards, start=1):
                norma = _parse_card_safely(card, page_url, posicion)
                if norma is None:
                    stats.descartadas += 1
                    continue
                if norma.document_id in vistos:
                    logger.warning("document_id=%s duplicado en %s; se descarta", norma.document_id, page_url)
                    stats.duplicadas += 1
                    continue
                vistos.add(norma.document_id)
                normas.append(norma)
                if norma.fecha_emision is None:
                    stats.fecha_emision_nula += 1
                # Salir del for también detiene el generador: no se piden más páginas.
                if limit is not None and len(normas) >= limit:
                    logger.info("Alcanzado --limit %d; no se piden más páginas", limit)
                    stats.normas = len(normas)
                    return normas, stats
    # Una página que falla tras los reintentos corta la corrida (saltarla
    # perdería sus normas sin aviso), pero no se pierde lo ya extraído.
    except requests.RequestException as exc:
        stats.corrida_incompleta = True
        logger.error(
            "Falló la descarga del listado tras los reintentos (%s); se conservan las %d normas ya extraídas",
            exc, len(normas),
        )

    stats.normas = len(normas)
    # Control de completitud: cada tarjeta listada (válida, descartada o
    # duplicada) cuenta en el total que declara el sitio. Si no cuadra, el
    # resultado no es confiable y la corrida se marca incompleta.
    if not stats.corrida_incompleta and stats.total_declarado is not None:
        procesadas = stats.normas + stats.descartadas + stats.duplicadas
        if procesadas != stats.total_declarado:
            stats.corrida_incompleta = True
            logger.error(
                "El sitio declara %d normas en total pero se procesaron %d",
                stats.total_declarado, procesadas,
            )
    return normas, stats
