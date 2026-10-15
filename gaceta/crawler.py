"""Descarga del listado: cliente HTTP con reintentos y recorrido de páginas."""

import logging
import time
from collections.abc import Iterator
from datetime import date
from itertools import count

import requests
from bs4 import BeautifulSoup

from gaceta.config import (
    BACKOFF_BASE_SECONDS,
    BASE_URL,
    DELAY_SECONDS,
    MAX_RETRIES,
    SEARCH_PATH_TEMPLATE,
    TIMEOUT_SECONDS,
    USER_AGENT,
)
from gaceta.parser import make_soup

logger = logging.getLogger(__name__)

# Fallas transitorias que vale la pena reintentar. Los 4xx no entran: repetir
# la misma petición no los arregla.
RETRYABLE_ERRORS = (requests.Timeout, requests.ConnectionError)
SERVER_ERROR_MIN_STATUS = 500


class HttpClient:
    """GET con pausa entre requests y reintentos con backoff ante timeouts y 5xx."""

    def __init__(
        self,
        delay_seconds: float = DELAY_SECONDS,
        max_retries: int = MAX_RETRIES,
        timeout_seconds: float = TIMEOUT_SECONDS,
        session: requests.Session | None = None,
    ) -> None:
        self._delay_seconds = delay_seconds
        self._max_retries = max_retries
        self._timeout_seconds = timeout_seconds
        self._session = session or requests.Session()
        self._session.headers["User-Agent"] = USER_AGENT
        self._last_request_at: float | None = None

    def _wait_politely(self) -> None:
        """Duerme lo necesario para que entre requests pasen al menos `delay_seconds`."""
        if self._last_request_at is None:
            return
        pendiente = self._delay_seconds - (time.monotonic() - self._last_request_at)
        if pendiente > 0:
            time.sleep(pendiente)

    def get_html(self, url: str) -> str:
        """Descarga `url` y la devuelve como texto. Lanza requests.RequestException
        si falla tras agotar los reintentos o ante un 4xx (no se reintenta)."""
        for intento in range(1, self._max_retries + 1):
            self._wait_politely()
            inicio = time.monotonic()
            self._last_request_at = inicio
            try:
                response = self._session.get(url, timeout=self._timeout_seconds)
            except RETRYABLE_ERRORS as exc:
                error: requests.RequestException = exc
            else:
                logger.debug("GET %s -> %d (%.2fs)", url, response.status_code, time.monotonic() - inicio)
                if response.status_code < SERVER_ERROR_MIN_STATUS:
                    # 2xx -> éxito; 4xx -> raise_for_status lanza sin reintentar.
                    response.raise_for_status()
                    return _decode(response.content, url)
                error = requests.HTTPError(f"HTTP {response.status_code} en {url}", response=response)

            # Llegar aquí significa una falla reintentable (timeout, conexión o 5xx).
            if intento == self._max_retries:
                raise requests.RequestException(f"GET {url} falló tras {intento} intentos: {error}") from error
            espera = BACKOFF_BASE_SECONDS * 2 ** (intento - 1)
            logger.warning(
                "GET %s falló (intento %d/%d): %s; reintento en %.0fs",
                url, intento, self._max_retries, error, espera,
            )
            time.sleep(espera)
        raise AssertionError("inalcanzable: el loop retorna o lanza")


def _decode(content: bytes, url: str) -> str:
    """Decodifica el cuerpo de la respuesta a texto.

    Se decodifican los bytes a mano en vez de usar `response.text`, porque el
    encoding que adivina requests puede no coincidir y corromper los acentos.
    """
    # El sitio sirve UTF-8 con BOM; 'utf-8-sig' lo descarta.
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        logger.warning("%s no es UTF-8 válido; se reemplazan los bytes inválidos", url)
        return content.decode("utf-8-sig", errors="replace")


def build_search_url(start: date, end: date, pagina: int = 1) -> str:
    """URL de la página `pagina` de la búsqueda por rango de fechas."""
    url = BASE_URL + SEARCH_PATH_TEMPLATE.format(start=start.isoformat(), end=end.isoformat())
    return url if pagina == 1 else f"{url}/page:{pagina}"


def iter_listing_pages(client: HttpClient, start: date, end: date) -> Iterator[tuple[str, BeautifulSoup]]:
    """Recorre el listado pidiendo page:1, page:2, ... sin fin.

    No se sigue el enlace 'siguiente' para no depender del HTML del paginador.
    Decidir cuándo parar es del llamador: una página fuera de rango devuelve
    otra vez la última, así que el fin se detecta cuando dejan de aparecer
    normas nuevas (ver etl.extract_normas).

    Genera (url, soup) por página. Al ser un generador, el llamador corta con
    `break`/`return` sin descargar páginas que no necesita.
    """
    for pagina in count(1):
        url = build_search_url(start, end, pagina)
        yield url, make_soup(client.get_html(url))
