# Nota técnica — Captación de federaciones deportivas

## Estado actual (v0.3): no automatizado

La página oficial del CSD con las federaciones deportivas españolas
(`apps1.csd.gob.es/WS_Portal/index.php?servicio=federaciones`) es una
**aplicación con JavaScript dinámico** (SPA). El listado no aparece en el
HTML inicial: lo carga el navegador desde una API JSON tras renderizar la página.

Por tanto, **el scraping con `requests` (que es lo que usa todo el resto del
sistema) no funciona aquí**. Hay que usar Playwright (navegador headless) o
realizar ingeniería inversa de la API JSON subyacente.

## Plan para la v0.4

Trabajo a realizar (estimación: 1 día de desarrollo, 0.5 día de prueba):

### 1. Inspección DevTools

Abrir Chrome → DevTools → pestaña Network → cargar la página. Identificar:

- La llamada XHR/Fetch que carga el listado de federaciones.
- Su URL exacta, método (GET/POST) y parámetros.
- El formato de respuesta (probablemente JSON).

Si la API es accesible directamente, **se prefiere la API JSON sobre Playwright**
(más rápido, más estable).

### 2. Opción A: API directa (si está accesible sin auth)

```python
import requests
r = requests.get("URL_API_DESCUBIERTA")
federaciones = r.json()
# Parsear estructura
```

### 3. Opción B: Playwright (si la API requiere headers/cookies complejos)

```python
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto("https://apps1.csd.gob.es/WS_Portal/index.php?servicio=federaciones")
    page.wait_for_selector(".federacion-item")  # ajustar al selector real
    federaciones = page.evaluate("""
        () => Array.from(document.querySelectorAll('.federacion-item')).map(el => ({
            nombre: el.querySelector('.nombre').innerText,
            web: el.querySelector('a').href,
            // ...
        }))
    """)
    browser.close()
```

### 4. Reutilización del pipeline existente

Una vez extraído el listado:

- Crear objetos `Negocio` con `segmento="federaciones_deportivas"`.
- Insertarlos en el JSON con el mismo formato que generan los scrapers Google Places.
- A partir de ahí, `extraer_emails.py` y `generar_csv.py` funcionan tal cual.

## Mientras tanto: estrategia manual para v0.3

Las federaciones deportivas españolas son ~65 nacionales. **A mano se hacen en una tarde.**

1. Abrir https://www.csd.gob.es/es/federaciones-y-asociaciones/federaciones-deportivas-espanolas/federaciones-espanolas
2. Hacer clic en cada federación, copiar nombre + web + email institucional a una hoja de cálculo.
3. Para las federaciones autonómicas (~1.000), priorizar las 5-10 más importantes por CCAA al principio.
4. Importar manualmente a Mautic y lanzar campaña con la plantilla `lopivi_federaciones.md`.

Esta vía manual es perfectamente válida para empezar mientras se desarrolla la v0.4.

## Por qué este enfoque no entra en v0.3

Tres razones:

1. **Calidad sobre cantidad**: en lugar de añadir un scraper a medias al paquete v0.3, preferimos entregar 5 segmentos automatizados sólidos.
2. **Las federaciones requieren mensaje y trato institucional distinto**: a diferencia de los segmentos masivos, aquí cada lead se trabaja a mano. Tener 65 nacionales en Excel es suficiente.
3. **Dependencia técnica adicional**: Playwright añade ~300 MB de dependencias (Chromium). No queremos meter eso en el paquete principal si no es estrictamente necesario.

## Cuando llegue v0.4

Crear un nuevo módulo en `core/scrapers_especiales/csd_federaciones.py` que:

- Use Playwright si la API no es accesible directamente.
- Genere objetos `Negocio` con `segmento="federaciones_deportivas"`.
- Escriba al mismo formato JSON que el resto del pipeline.
- Pueda invocarse desde `scripts/buscar_federaciones.py`.
