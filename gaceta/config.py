"""Constantes del ETL. Todo valor ajustable vive aquí, con su motivo."""

BASE_URL = "http://www.gacetaoficialdebolivia.gob.bo"

# Búsqueda por rango de fechas: /normas/buscarFecha/{inicio}/{fin}[/page:N]
SEARCH_PATH_TEMPLATE = "/normas/buscarFecha/{start}/{end}"

# User-Agent de navegador estándar: el sitio no bloquea peticiones simples.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Sitio antiguo e intermitente: pausa corta entre requests para no saturarlo.
DELAY_SECONDS = 0.4

# Reintentos ante timeouts, errores de conexión y respuestas 5xx.
MAX_RETRIES = 3

# Espera antes del reintento N = BACKOFF_BASE_SECONDS * 2 ** (N - 1) -> 1s, 2s, ...
BACKOFF_BASE_SECONDS = 1.0

# Timeout por request (conexión + lectura), en segundos.
TIMEOUT_SECONDS = 30.0

# Páginas seguidas sin normas nuevas que marcan el fin del listado. Una página
# fuera de rango repite la última, así que con 1 bastaría; 2 tolera una página
# intermedia que repita normas si el sitio reordena mientras se pagina.
PAGINAS_SIN_NOVEDAD_MAX = 2
