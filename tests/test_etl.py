"""Tests del ETL completo (paginación, parada y resiliencia de red), sin red real.

Se reemplaza la sesión HTTP por una falsa que sirve los fixtures. Como en el
sitio real, una página fuera de rango devuelve otra vez la última.
"""

import logging
from datetime import date
from pathlib import Path

import pytest
import requests

from gaceta import crawler, etl

FIXTURES = Path(__file__).parent / "fixtures"
PAGE1 = FIXTURES / "listado_2026-08_page1.html"
PAGE2 = FIXTURES / "listado_2026-08_page2.html"
RANGO = (date(2026, 8, 1), date(2026, 8, 31))


class FakeResponse:
    """Lo mínimo de requests.Response que usa HttpClient."""

    def __init__(self, status_code: int, content: bytes = b"") -> None:
        self.status_code = status_code
        self.content = content

    def raise_for_status(self) -> None:
        if 400 <= self.status_code < 500:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FixtureSession(requests.Session):
    """Sirve `paginas[N - 1]` para page:N; más allá del final, repite la última."""

    def __init__(self, paginas: list[bytes]) -> None:
        super().__init__()
        self.paginas = paginas
        self.urls: list[str] = []

    def get(self, url, timeout):
        self.urls.append(url)
        numero = int(url.rsplit("page:", 1)[1]) if "page:" in url else 1
        return FakeResponse(200, self.paginas[min(numero, len(self.paginas)) - 1])


def _run(monkeypatch, session: requests.Session):
    """Corre extract_normas sobre el rango de los fixtures con `session` y sin esperas."""
    monkeypatch.setattr(crawler, "BACKOFF_BASE_SECONDS", 0.0)
    monkeypatch.setattr(etl, "HttpClient", lambda: crawler.HttpClient(delay_seconds=0, session=session))
    return etl.extract_normas(*RANGO)


def test_para_tras_paginas_sin_normas_nuevas(monkeypatch):
    session = FixtureSession([PAGE1.read_bytes(), PAGE2.read_bytes()])

    normas, stats = _run(monkeypatch, session)

    # page:1 y page:2 traen normas; page:3 y page:4 repiten la 2 y cortan.
    assert [url.rsplit("/", 1)[1] for url in session.urls] == ["2026-08-31", "page:2", "page:3", "page:4"]
    assert len(normas) == 32
    assert stats.duplicadas == 0  # las páginas repetidas no cuentan como duplicados
    assert stats.corrida_incompleta is False


def test_pagina_sin_tarjetas_marca_corrida_incompleta(monkeypatch, caplog):
    # Simula un rediseño: el enlace 'Ver Norma', que ancla cada tarjeta, cambia de ruta.
    html = PAGE1.read_bytes().replace(b"/normas/verGratis_gob/", b"/normas/ver/")
    session = FixtureSession([html])

    with caplog.at_level(logging.ERROR):
        normas, stats = _run(monkeypatch, session)

    assert normas == []
    assert stats.corrida_incompleta is True
    assert len(session.urls) == 1
    assert "no trae ninguna norma" in caplog.text


def test_rango_sin_normas_no_es_error(monkeypatch):
    # Encabezado real del sitio para un rango vacío.
    html = b"<html><body>P&aacute;gina 0 de 1 // mostrando normas 0 - 0 // 0 en total<p></body></html>"
    session = FixtureSession([html])

    normas, stats = _run(monkeypatch, session)

    assert normas == []
    assert stats.corrida_incompleta is False


def test_total_que_no_cuadra_marca_corrida_incompleta(monkeypatch, caplog):
    # Solo existe la página 1 (20 normas), pero su encabezado declara 32.
    session = FixtureSession([PAGE1.read_bytes()])

    with caplog.at_level(logging.ERROR):
        normas, stats = _run(monkeypatch, session)

    assert len(normas) == 20
    assert stats.corrida_incompleta is True
    assert "declara 32 normas en total pero se procesaron 20" in caplog.text


@pytest.mark.parametrize(
    ("href_original", "campo"),
    [("/normas/descargarNrms/", "anexo_url"), ("/edicions/view/", "edicion")],
)
def test_norma_sin_campo_obligatorio_se_descarta_y_la_corrida_sigue(monkeypatch, caplog, href_original, campo):
    # Se rompe el enlace en la primera tarjeta de la página 1 (id 281300).
    html = PAGE1.read_bytes().replace(href_original.encode(), b"/otra/ruta/", 1)
    session = FixtureSession([html])

    with caplog.at_level(logging.ERROR):
        normas, stats = _run(monkeypatch, session)

    assert len(normas) == 19
    assert stats.descartadas == 1
    errores = [r.getMessage() for r in caplog.records if "descartada" in r.getMessage()]
    assert len(errores) == 1
    assert "document_id=281300" in errores[0] and "posición 1" in errores[0] and campo in errores[0]


class StatusSession(requests.Session):
    """Responde siempre el mismo status HTTP."""

    def __init__(self, status: int) -> None:
        super().__init__()
        self.status = status
        self.urls: list[str] = []

    def get(self, url, timeout):
        self.urls.append(url)
        return FakeResponse(self.status)


def test_error_4xx_no_se_reintenta(monkeypatch, caplog):
    session = StatusSession(404)

    with caplog.at_level(logging.ERROR):
        normas, stats = _run(monkeypatch, session)

    assert len(session.urls) == 1  # un 4xx no se arregla repitiendo la petición
    assert normas == []
    assert stats.corrida_incompleta is True
    assert "Falló la descarga" in caplog.text


class FlakySession(requests.Session):
    """Página 1: 503 y luego OK. Página 2: timeout siempre."""

    def __init__(self) -> None:
        super().__init__()
        self.urls: list[str] = []

    def get(self, url, timeout):
        self.urls.append(url)
        if "page:2" in url:
            raise requests.Timeout("read timed out")
        if len(self.urls) == 1:
            return FakeResponse(503)
        return FakeResponse(200, PAGE1.read_bytes())


def test_red_falla_a_mitad_de_corrida_conserva_lo_extraido(monkeypatch, caplog):
    session = FlakySession()

    with caplog.at_level(logging.WARNING):
        normas, stats = _run(monkeypatch, session)

    # Página 1: 1 intento fallido (503) + 1 exitoso. Página 2: 3 intentos con timeout.
    assert len(session.urls) == 5
    assert len(normas) == 20
    assert stats.corrida_incompleta is True
    assert stats.paginas == 1

    errores = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errores) == 1
    assert "page:2" in errores[0].getMessage()
    reintentos = [r for r in caplog.records if r.levelno == logging.WARNING and "intento" in r.getMessage()]
    assert len(reintentos) == 3  # 1 por el 503 + 2 por los timeouts antes de rendirse
