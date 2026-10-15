"""Tests del parser contra HTML real del listado guardado localmente (sin red).

Fixtures: las 2 páginas de buscarFecha/2026-08-01/2026-08-31, descargadas el
2026-10-14. La página 2 incluye una norma con Clasificación y anexo (RM 155,
id 281338); la página 1 empieza con la tarjeta de HTML mal anidado (id 281300)
que motivó usar lxml.
"""

import logging
from pathlib import Path

import pytest

from gaceta.models import Norma
from gaceta.parser import _absolute, card_document_id, find_cards, make_soup, parse_card, parse_resumen
from gaceta.utils.dates import parse_fecha_espanol

FIXTURES = Path(__file__).parent / "fixtures"
PAGE1 = FIXTURES / "listado_2026-08_page1.html"
PAGE2 = FIXTURES / "listado_2026-08_page2.html"


def _load(path: Path) -> str:
    """Lee un fixture igual que el crawler decodifica la respuesta (UTF-8 con BOM)."""
    return path.read_text(encoding="utf-8-sig")


def _normas_by_id(html: str) -> dict:
    """Parsea todas las tarjetas del HTML y las indexa por document_id."""
    return {norma.document_id: norma for norma in map(parse_card, find_cards(make_soup(html)))}


def test_parse_card_con_clasificacion_y_anexo():
    normas = _normas_by_id(_load(PAGE2))
    rm = normas["281338"]

    assert rm.titulo == "Resolución Ministerial N° 155"
    assert rm.edicion == "2093NEC"
    assert rm.fecha_publicacion == "2026-09-02"
    assert rm.fecha_emision == "2026-08-28"  # distinta de la de publicación
    assert rm.epigrafe.startswith("Aprueba el “REGLAMENTO QUE ESTABLECE LAS ESPECIFICACIONES TÉCNICAS")
    assert "AGOSTO DE 2026" not in rm.epigrafe  # el prefijo de fecha no queda en el epígrafe
    assert rm.entidad_emisora == "MINISTERIO_DE_HIDROCARBUROS"
    assert rm.anexo_url == "http://www.gacetaoficialdebolivia.gob.bo/normas/descargarNrms/281338"
    assert rm.fuente_url == "http://www.gacetaoficialdebolivia.gob.bo/normas/verGratis_gob/281338"
    assert len(rm.anexos_adicionales) == 1
    assert rm.anexos_adicionales[0].url == "http://www.gacetaoficialdebolivia.gob.bo/anexos/descargarAnexo/965"
    assert rm.anexos_adicionales[0].descripcion.startswith("ANEXO - REGLAMENTO")


def test_parse_card_sin_clasificacion_ni_anexos():
    normas = _normas_by_id(_load(PAGE1))
    dp = normas["281300"]

    assert dp.titulo == "Decreto Presidencial N° 5668"
    assert dp.fecha_publicacion == "2026-08-03"
    assert dp.fecha_emision == "2026-08-03"
    assert dp.entidad_emisora is None
    assert dp.anexos_adicionales == []
    # El epígrafe del pie de página (DS 690 de 2010) no contamina ninguna norma.
    assert all("2010" not in (n.fecha_emision or "") for n in normas.values())


def test_tarjetas_y_resumen():
    soup1, soup2 = make_soup(_load(PAGE1)), make_soup(_load(PAGE2))

    assert len(find_cards(soup1)) == 20
    assert len(find_cards(soup2)) == 12
    resumen = parse_resumen(soup1)
    assert (resumen.pagina, resumen.paginas, resumen.total_normas) == (1, 2, 32)


def test_fecha_emision_invalida_queda_null_con_warning(caplog):
    html = _load(PAGE2).replace("28 DE AGOSTO DE 2026", "31 DE FEBRERO DE 2026")

    with caplog.at_level(logging.WARNING, logger="gaceta.parser"):
        rm = _normas_by_id(html)["281338"]

    assert rm.fecha_emision is None
    assert any("281338" in r.getMessage() and "fecha_emision" in r.getMessage() for r in caplog.records)



def _rm155_con_fecha(fecha: str, con_strong: bool = True) -> Norma:
    """RM 155 (281338) real, con el prefijo de fecha del epígrafe reemplazado por `fecha`.

    Con `con_strong=False` la fecha queda como texto plano, sin el <strong>.
    """
    soup = make_soup(_load(PAGE2))
    card = next(c for c in find_cards(soup) if card_document_id(c) == "281338")
    strong = card.find("div", class_="contentpaneopen").find("strong")
    if con_strong:
        strong.string = fecha
    else:
        strong.replace_with(fecha)
    return parse_card(card)


@pytest.mark.parametrize(
    ("fecha", "con_strong", "esperado"),
    [
        ("28 de agosto de 2026 .- ", True, "2026-08-28"),
        ("1° DE SEPTIEMBRE DE 2026 .- ", True, "2026-09-01"),
        ("01 DE AGOSTO DE 2026 .- ", True, "2026-08-01"),
        ("28\xa0DE\xa0AGOSTO\xa0DE\xa02026 .- ", True, "2026-08-28"),
        ("29 DE FEBRERO DE 2028 .- ", True, "2028-02-29"),
        ("29 DE FEBRERO DE 2026 .- ", True, None),
        ("0 DE AGOSTO DE 2026 .- ", True, None),
        ("28 DE AGOSTO 2026 .- ", True, None),
        ("", True, None),
        ("28 DE AGOSTO DE 2026 .- ", False, "2026-08-28"),
        ("31 DE FEBRERO DE 2026 .- ", False, None),
    ],
)
def test_variantes_de_fecha_emision(fecha, con_strong, esperado, caplog):
    with caplog.at_level(logging.WARNING, logger="gaceta.parser"):
        rm = _rm155_con_fecha(fecha, con_strong)

    assert rm.fecha_emision == esperado
    assert rm.fecha_publicacion == "2026-09-02"  # nunca se mezcla con la de emisión
    assert rm.epigrafe.startswith("Aprueba el")  # el prefijo de fecha nunca queda en el epígrafe
    if esperado is None:
        assert any("281338" in r.getMessage() for r in caplog.records)

@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("28 DE AGOSTO DE 2026 .- ", "2026-08-28"),
        ("3 de septiembre de 2026", "2026-09-03"),
        ("1RO DE SETIEMBRE DE 2026", "2026-09-01"),
        ("31 DE FEBRERO DE 2026", None),
        ("28 DE AGOSTITO DE 2026", None),
        ("Aprueba el reglamento", None),
    ],
)
def test_parse_fecha_espanol(texto, esperado):
    assert parse_fecha_espanol(texto) == esperado


@pytest.mark.parametrize(
    ("href", "esperado"),
    [
        ("/normas/descargarNrms/281300", "http://www.gacetaoficialdebolivia.gob.bo/normas/descargarNrms/281300"),
        (" /anexos/descargarAnexo/9 ", "http://www.gacetaoficialdebolivia.gob.bo/anexos/descargarAnexo/9"),
        ("normas/verGratis_gob/1", "http://www.gacetaoficialdebolivia.gob.bo/normas/verGratis_gob/1"),
        ("http://www.gacetaoficialdebolivia.gob.bo/normas/verGratis_gob/1",
         "http://www.gacetaoficialdebolivia.gob.bo/normas/verGratis_gob/1"),
    ],
)
def test_absolute(href, esperado):
    assert _absolute(href) == esperado
