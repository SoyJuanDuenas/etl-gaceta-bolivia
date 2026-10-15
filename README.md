# ETL — Actos Administrativos de Bolivia

Extrae las normas publicadas en la [Gaceta Oficial de Bolivia](http://www.gacetaoficialdebolivia.gob.bo)
en un rango de fechas y las guarda como JSON (`output.json`).

## Cómo correrlo

Requiere Python 3.10+ y git. Se clona el repo, se instalan las dependencias en un entorno virtual y se corre
`main.py` con el rango de fechas. El JSON queda en `output.json` (o en la ruta de `--output`; con `--output -` se
imprime en stdout), y cada corrida deja un log detallado en `logs/etl_<timestamp>.log`. El exit code es 1 si hubo
algún ERROR (por ejemplo, una norma descartada) o si la corrida quedó incompleta; aun así se escribe el JSON.

```bash
git clone https://github.com/SoyJuanDuenas/etl-gaceta-bolivia.git
cd etl-gaceta-bolivia

python -m venv .venv
.venv/Scripts/activate            # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt

python main.py --start-date 2026-08-01 --end-date 2026-08-31            # genera output.json (32 normas)
python main.py --start-date 2026-08-01 --end-date 2026-08-31 --limit 5 --output -
python -m pytest                                                         # tests sin red (parser, paginación, red)
```

## Cómo funciona

- **Una request por página, ninguna por norma.** Se usa `normas/buscarFecha/{inicio}/{fin}`: cada tarjeta del
  listado ya trae todos los campos (edición, fecha de publicación, título, epígrafe, clasificación, anexos y los
  enlaces "Ver Norma" y "Descargar PDF").
- **Paginación sin un máximo fijo ni selectores.** Se piden `page:1`, `page:2`, ... y se para cuando 2 páginas
  seguidas no traen ningún `document_id` nuevo: una página inexistente devuelve **otra vez la última**, nunca una
  vacía. No se sigue el enlace "siguiente >>": si el sitio cambiara el paginador, la corrida pararía en la página 1
  sin avisar. Una página sin tarjetas (salvo un rango con "0 en total") o un total procesado que no cuadra con el
  "N en total" del sitio marcan la corrida como incompleta. Probado con todo 2025: 23 páginas, 446 de 446.
- **Selectores por patrón de `href`** (`verGratis_gob/{id}`, `descargarNrms/{id}`, `edicions/view/`,
  `buscar_comp/`, `descargarAnexo/{id}`) en lugar de clases CSS de maquetación, y siempre dentro de la tarjeta de
  cada norma.
- **Red:** pausa de 0.4 s entre requests y hasta 3 intentos con backoff exponencial ante timeouts, errores de
  conexión y 5xx (los 4xx no se reintentan). Si una página falla definitivamente, se registra un ERROR y se
  escribe lo ya extraído.

```
main.py                    CLI: argumentos, logging, escritura del JSON, resumen
gaceta/config.py           constantes (URL, delay, reintentos, timeout, fin del listado)
gaceta/crawler.py          cliente HTTP con reintentos + recorrido de páginas
gaceta/parser.py           tarjeta HTML -> Norma; encabezado del listado
gaceta/etl.py              orquestación, fin del listado, deduplicación, estadísticas
gaceta/models.py           dataclasses Norma / Anexo
gaceta/utils/              fechas en español, texto, logging
tests/                     parser contra HTML real guardado + paginación y red simuladas
```

## Decisiones y supuestos

- **Rango = lo que devuelve el filtro del sitio.** `buscarFecha/2026-08-01/2026-08-31` devuelve 32 normas, y 2 de
  ellas tienen "Fecha de Publicación" de septiembre (Ley 1760 y RM 155). Se conservan las 32 tal como las asocia el
  filtro, y cada una lleva su `fecha_publicacion` real en el JSON; no se filtra después.
- **`fecha_emision` y `epigrafe`:** la fecha sale del `<strong>` con que empieza el epígrafe (`28 DE AGOSTO DE
  2026 .-`): mes en español (incluida la variante `SETIEMBRE` y ordinales como `1RO`), validada con `datetime.date`.
  Si no es reconocible o es inválida (`31 DE FEBRERO`), queda en `null` con un WARNING que incluye el `document_id`
  y el texto original; nunca se copia `fecha_publicacion` como reemplazo. El `epigrafe` se guarda **sin** ese
  prefijo, porque la fecha ya está estructurada en `fecha_emision`.
- **Campos faltantes:** `entidad_emisora` queda en `null` cuando la norma no trae "Clasificación" (la mayoría no la
  trae). `anexos_adicionales` siempre es una lista (`[]` si no hay): las descripciones (`<li>`) se emparejan en orden
  con los enlaces "Descargar Anexo", con un warning si los conteos no cuadran. Si falta un campo obligatorio (id,
  título, fecha de publicación, epígrafe, PDF, edición), la norma se descarta con un ERROR que indica página,
  posición y `document_id`, y la corrida sigue.
- **URLs:** `fuente_url` es la vista propia de la norma (`/normas/verGratis_gob/{id}`, el enlace "Ver Norma"),
  aunque los campos se leen de su tarjeta en el listado: es estable y citable, mientras que la URL del listado
  depende del rango y de la página. Para `anexo_url` se aceptan las dos variantes de "Descargar PDF"
  (`descargarNrms` y, en normas antiguas, `descargarPdf`). Todas las URLs se guardan absolutas.
- **HTML roto y codificación:** se parsea con `lxml`, que repara el anidado como un navegador; con `html.parser`, un
  `</b>` huérfano dejaba fuera de su tarjeta a la primera norma de la página. El HTML llega en UTF-8 con BOM y se
  decodifica como `utf-8-sig`; el JSON se escribe con `ensure_ascii=False` para conservar los acentos.

## Pregunta de diseño: corrida diaria incremental e idempotente

- **Upsert por `document_id`** (PK en el destino, `INSERT ... ON CONFLICT DO UPDATE`): reprocesar una norma la
  actualiza en vez de duplicarla, así que la corrida es idempotente por construcción.
- **Ventana deslizante** (`buscarFecha/{hoy-7}/{hoy}`) en lugar de "desde la última vez", porque el sitio publica
  con retraso; el upsert absorbe el solapamiento.
- **Hash del registro normalizado:** si no cambió, no se reescribe ni se vuelve a descargar el PDF; si cambió
  (por ejemplo, se le agregó un anexo), se actualiza con `updated_at`.
- **Tabla de control por corrida** (rango, normas, errores, `corrida_incompleta`): si una queda incompleta, la
  siguiente repite la misma ventana sin riesgo de duplicar.
