# CGD — Sistema de captación v0.4

Esta versión añade dos capacidades al sistema:

1. **Módulo de cruce con Google Places** (`core/cruce_places.py`) — Listo y probado.
2. **Esqueleto del scraper RAED** (`core/scraper_raed.py`) — Pendiente de completar tras la captura DevTools.

## Cambios respecto a v0.3.1

| Concepto | v0.3.1 | v0.4 |
|---|---|---|
| Fuentes de descubrimiento | Solo Google Places | Google Places + **cualquier listado externo** (RAED, CSV, etc.) |
| Cruce nombre+localidad → datos Google | No | **Sí, con matching robusto** |
| Filtro de tipos de negocio incompatibles | No | **Sí** (descarta bares, restaurantes, etc.) |
| Soporte para fuentes oficiales (RAED, registros) | No | **Esqueleto listo** |

## Casos de uso inmediatos

### Caso 1 — Enriquecer los 23 colegios del CGCAFE (v0.1)

El CSV de la v0.1 tiene nombre y dirección pero no email ni web validados. Con este módulo:

```bash
python scripts/cruzar_csv.py \
    --input ../cgd_scraper_v01/data/colegios_cgcafe_inicial.csv \
    --segmento admin_fincas \
    --campo-nombre nombre \
    --campo-localidad localidad \
    --campo-provincia provincia \
    --fuente-origen cgcafe
```

Salida: nuevo CSV con cada colegio ENRIQUECIDO con datos de Google (web, teléfono, rating, place_id). Después se puede ejecutar `extraer_emails.py` para sacar emails de las webs.

### Caso 2 — Enriquecer un listado manual de federaciones autonómicas

Si tu equipo tiene un Excel con federaciones autonómicas trabajadas a mano, basta con:

1. Guardar el Excel como CSV con columnas mínimas: `nombre`, `localidad`.
2. Ejecutar el mismo script:

```bash
python scripts/cruzar_csv.py \
    --input mi_listado_federaciones.csv \
    --segmento clubes_deportivos \
    --fuente-origen manual
```

### Caso 3 — Enriquecer salida del scraper RAED (cuando esté listo)

Cuando los técnicos completen la captura DevTools y se programe el scraper RAED:

```bash
# Paso 1: descargar RAED (cuando esté implementado)
python scripts/descargar_raed.py --provincia Sevilla --tipo Club_deportivo

# Paso 2: cruzar con Google
python scripts/cruzar_csv.py \
    --input data/raed_sevilla_clubes_AAAAMMDD.csv \
    --segmento clubes_deportivos \
    --campo-id numero_inscripcion \
    --fuente-origen raed
```

## Características del matching

El cruce con Google es robusto:

- **Tolerante a variaciones tipográficas**: "C.D. Triana" matchea con "Club Deportivo Triana".
- **Tolerante a acentos y mayúsculas**: "Náutico" con "Naútico", "GARCÍA" con "Garcia".
- **Conservador frente a entidades equivocadas**: prefiere NO matchear antes que matchear erróneamente. Esto protege la reputación de envío.
- **Filtra negocios de tipo incompatible**: si Google devuelve un bar llamado "Atlético Triana" para una búsqueda de club, se descarta automáticamente.
- **Excepciones por segmento**: para campamentos, se aceptan resultados tipo "lodging" o "tourist_attraction"; para clubes no.

## Estado del scraper RAED

Bloqueado a la espera de la captura DevTools. Ver `docs/guia_devtools_raed.md` para que el equipo de sistemas pueda hacerla en 10-15 minutos.

Una vez completada la captura, el desarrollo del scraper toma 1-2 horas reales. El esqueleto en `core/scraper_raed.py` está listo y solo hay que rellenar las funciones marcadas con `TODO`.

## Cómo se conecta esto con lo que ya teníamos

Es importante entender que **NO sustituye al v0.3**, lo complementa:

- **Descubrimiento masivo de un segmento** (sin lista previa) → `scripts/buscar.py --segmento X` (del v0.3).
- **Enriquecimiento de una lista que ya tienes** → `scripts/cruzar_csv.py` (este v0.4).
- **Extracción de emails** y scoring → mismos `extraer_emails.py` y `generar_csv.py` del v0.3 sobre los JSON resultantes.

Es decir, el cruce con Places es una **fuente más** que el sistema puede usar, no un nuevo flujo paralelo.

## Próxima sesión

Cuando tus técnicos te pasen la captura DevTools del RAED (`captura_raed.txt`), envíamela y completo el scraper en una sola sesión.
