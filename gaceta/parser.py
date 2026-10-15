"""Parseo del listado de normas (/normas/buscarFecha/...).

Cada norma del listado es una tarjeta (`div.card`) que trae todos los campos
requeridos. Los elementos se localizan por el patrón de su `href` (estable)
y siempre dentro de la tarjeta, nunca en la página entera: el pie de página
trae un epígrafe ajeno (DS 690) que no debe confundirse con el de la norma.
"""

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from itertools import zip_longest

from bs4 import BeautifulSoup, Tag

from gaceta.config import BASE_URL
from gaceta.models import Anexo, Norma
from gaceta.utils.dates import parse_fecha_espanol
from gaceta.utils.text import normalize_space

logger = logging.getLogger(__name__)

# Patrones de href que identifican cada elemento de la tarjeta. Se anclan con
# `$` donde el id es lo último de la ruta, para no confundir p. ej.
# "verGratis_gob/{id}" (Ver Norma) con "verGratis_gob1/{id}" (Descargar Word).
VER_NORMA_HREF_RE = re.compile(r"/normas/verGratis_gob/(\d+)$")
# "Descargar PDF": la mayoría usa descargarNrms; algunas normas antiguas, descargarPdf.
PDF_HREF_RE = re.compile(r"/normas/descargar(?:Nrms|Pdf)/\d+$")
EDICION_HREF_RE = re.compile(r"/edicions/view/")
CLASIFICACION_HREF_RE = re.compile(r"/normas/buscar_comp/")
ANEXO_HREF_RE = re.compile(r"/anexos/descargarAnexo/\d+$")

# "Fecha de Publicación: 2026-08-03" en la cabecera de la tarjeta (ya en ISO).
FECHA_PUBLICACION_RE = re.compile(r"Fecha de Publicaci[oó]n:\s*(\d{4}-\d{2}-\d{2})")
# Encabezado del listado: "Página 1 de 2 // mostrando normas 1 - 20 // 32 en total"
RESUMEN_RE = re.compile(r"P[aá]gina\s+(\d+)\s+de\s+(\d+)\s*//[^/]*//\s*(\d+)\s+en total")


class ParseError(Exception):
    """Una tarjeta no trae un campo obligatorio."""


@dataclass(frozen=True)
class ResumenListado:
    """Encabezado del listado: página actual, total de páginas y total de normas."""

    pagina: int
    paginas: int
    total_normas: int


def make_soup(html: str) -> BeautifulSoup:
    """Parsea el HTML de una página del listado."""
    # lxml repara el HTML mal anidado como un navegador. Con 'html.parser'
    # un </b> huérfano saca la primera tarjeta de su div.card y se pierde.
    return BeautifulSoup(html, "lxml")


def _href_matching(pattern: re.Pattern[str]) -> Callable[[str | None], bool]:
    """Filtro para `find`/`find_all(href=...)`: el href (sin espacios) cumple `pattern`."""
    return lambda href: bool(href) and pattern.search(href.strip()) is not None


def _absolute(href: str) -> str:
    """Convierte un href del sitio en URL absoluta.

    El sitio usa rutas relativas a la raíz ("/normas/..."); un href que ya
    es absoluto se deja igual.
    """
    href = href.strip()
    if href.startswith(("http://", "https://")):
        return href
    return f"{BASE_URL}/{href.lstrip('/')}"


def parse_resumen(soup: BeautifulSoup) -> ResumenListado | None:
    """Lee 'Página X de N // ... // T en total'. None si el encabezado no aparece."""
    match = RESUMEN_RE.search(soup.get_text(" "))
    if not match:
        return None
    pagina, paginas, total = (int(valor) for valor in match.groups())
    return ResumenListado(pagina=pagina, paginas=paginas, total_normas=total)


def find_cards(soup: BeautifulSoup) -> list[Tag]:
    """Tarjetas de normas: el `div.card` que contiene cada enlace 'Ver Norma'.

    No se usa `div.card` a secas porque el sitio usa esa clase también en
    otros bloques (carrusel, accesos rápidos).
    """
    cards: list[Tag] = []
    # Se deduplica por identidad del nodo (id), no por igualdad: BeautifulSoup
    # compara Tags por contenido y es más caro.
    seen: set[int] = set()
    for link in soup.find_all("a", href=_href_matching(VER_NORMA_HREF_RE)):
        card = link.find_parent("div", class_="card")
        if card is None:
            logger.warning("Enlace 'Ver Norma' fuera de una tarjeta, se ignora: %s", link.get("href"))
            continue
        if id(card) not in seen:
            seen.add(id(card))
            cards.append(card)
    return cards


def card_document_id(card: Tag) -> str | None:
    """ID numérico de la norma desde el enlace 'Ver Norma'. None si no existe."""
    link = card.find("a", href=_href_matching(VER_NORMA_HREF_RE))
    if link is None:
        return None
    return VER_NORMA_HREF_RE.search(link["href"].strip()).group(1)


def _required(valor: str | None, campo: str) -> str:
    """Devuelve `valor` o lanza ParseError si viene vacío (campo obligatorio)."""
    if not valor:
        raise ParseError(f"falta el campo obligatorio '{campo}'")
    return valor


def _link_text(card: Tag, pattern: re.Pattern[str]) -> str | None:
    """Texto del primer enlace de la tarjeta cuyo href cumple `pattern`; None si no hay."""
    link = card.find("a", href=_href_matching(pattern))
    return normalize_space(link.get_text()) or None if link else None


def _link_url(card: Tag, pattern: re.Pattern[str]) -> str | None:
    """URL absoluta del primer enlace de la tarjeta cuyo href cumple `pattern`; None si no hay."""
    link = card.find("a", href=_href_matching(pattern))
    return _absolute(link["href"]) if link else None


def _parse_epigrafe(card: Tag) -> tuple[str | None, str | None]:
    """Devuelve (texto de la fecha embebida, epígrafe sin el prefijo de fecha).

    El epígrafe es el primer `div.contentpaneopen` de la tarjeta (los anexos
    usan la misma clase, pero van después). La fecha viene en un <strong> al
    inicio: "<strong>28 DE AGOSTO DE 2026 .- </strong>Aprueba ...".
    """
    contenedor = card.find("div", class_="contentpaneopen")
    if contenedor is None:
        return None, None
    parrafo = contenedor.find("p") or contenedor
    texto = normalize_space(parrafo.get_text(" "))
    strong = parrafo.find("strong")
    prefijo = normalize_space(strong.get_text(" ")) if strong else ""
    # Solo se trata el <strong> como fecha si está al inicio; un <strong> en
    # medio del texto sería énfasis y no debe recortarse del epígrafe.
    if prefijo and texto.startswith(prefijo):
        return prefijo, texto.removeprefix(prefijo).strip() or None
    # Sin <strong>: se intenta leer la fecha al inicio del texto completo.
    return texto, texto or None


def _parse_anexos(card: Tag, document_id: str) -> list[Anexo]:
    """Sección 'ANEXO(S)': descripciones en <li> y enlaces 'Descargar Anexo', en orden."""
    links = card.find_all("a", href=_href_matching(ANEXO_HREF_RE))
    if not links:
        return []
    # Se ubica la sección por los enlaces de descarga (estables) y no por el
    # rótulo "ANEXO(S) :", cuyo texto y espaciado varían.
    lista = links[0].find_parent("ul")
    descripciones = [normalize_space(li.get_text(" ")) or None for li in lista.find_all("li")] if lista else []
    if len(descripciones) != len(links):
        logger.warning(
            "document_id=%s: %d descripciones de anexo para %d enlaces; se emparejan en orden",
            document_id, len(descripciones), len(links),
        )
    # zip_longest no pierde enlaces si faltan descripciones (quedan en None);
    # las descripciones sin enlace se descartan porque sin url no sirven.
    return [
        Anexo(descripcion=descripcion, url=_absolute(link["href"]))
        for descripcion, link in zip_longest(descripciones, links)
        if link is not None
    ]


def parse_card(card: Tag) -> Norma:
    """Convierte una tarjeta del listado en una Norma.

    Lanza ParseError si falta un campo obligatorio. Una fecha de emisión
    ausente o inválida no es error: queda en None con un warning.
    """
    # El id va primero: da contexto a todos los logs de esta norma.
    document_id = _required(card_document_id(card), "document_id")

    # La cabecera es lo primero de la tarjeta, así que la primera coincidencia es la buena.
    match_publicacion = FECHA_PUBLICACION_RE.search(card.get_text(" "))
    fecha_publicacion = match_publicacion.group(1) if match_publicacion else None

    titulo_tag = card.find("h6")
    titulo = normalize_space(titulo_tag.get_text(" ")) if titulo_tag else None

    texto_fecha, epigrafe = _parse_epigrafe(card)
    fecha_emision = parse_fecha_espanol(texto_fecha) if texto_fecha else None
    if fecha_emision is None:
        logger.warning(
            "document_id=%s: fecha_emision no reconocida o inválida en %r; se guarda null",
            document_id, (texto_fecha or "")[:80],
        )

    # Los _required se evalúan aquí para reportar el primer campo obligatorio que falte.
    norma = Norma(
        document_id=document_id,
        titulo=_required(titulo, "titulo"),
        fecha_publicacion=_required(fecha_publicacion, "fecha_publicacion"),
        fecha_emision=fecha_emision,
        epigrafe=_required(epigrafe, "epigrafe"),
        anexo_url=_required(_link_url(card, PDF_HREF_RE), "anexo_url"),
        edicion=_required(_link_text(card, EDICION_HREF_RE), "edicion"),
        entidad_emisora=_link_text(card, CLASIFICACION_HREF_RE),
        fuente_url=_absolute(f"/normas/verGratis_gob/{document_id}"),
        anexos_adicionales=_parse_anexos(card, document_id),
    )
    logger.debug("document_id=%s parseada: %s", document_id, norma.titulo)
    return norma
