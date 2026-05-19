# PPMailing - Sistema de captacion B2B

Repositorio operativo del sistema de captacion B2B usado para generar listados CSV de leads a partir de Google Places, webs publicas y fuentes oficiales como RAED.

El proyecto no es una aplicacion web. Es un conjunto de scripts Python ejecutados por consola que generan ficheros intermedios JSON y salidas finales CSV en el directorio `data/`.

## Estado

- Version desplegada: `v0.4`
- Runtime: Python 3.10 o superior
- Sistema probado en Debian
- Entrada principal: Google Places API
- Fuente oficial soportada: RAED, Registro Andaluz de Entidades Deportivas
- Salidas: JSON enriquecido, CSV de leads, logs de ejecucion

## Estructura del proyecto

```text
.
├── config/                 # Configuracion general, ciudades y segmentos
├── core/                   # Logica de scraping, parsing, scoring y RAED
├── docs/                   # Guias tecnicas de apoyo
├── plantillas_email/       # Plantillas comerciales por segmento
├── scripts/                # Comandos ejecutables
├── data/                   # Salidas generadas, no versionadas
├── logs/                   # Logs de ejecucion, no versionados
├── requirements.txt
├── run.sh                  # Wrapper para cargar .env y ejecutar scripts
└── .env.example            # Plantilla de variables de entorno
```

Los directorios `data/`, `logs/`, `venv/` y `.env` estan excluidos del repositorio por seguridad y por contener datos generados o secretos.

## Requisitos del servidor

Paquetes minimos en Debian:

```bash
apt-get update
apt-get install -y python3 python3-venv python3-pip git ca-certificates
```

Para inspeccion o tratamiento manual de entregables puede ser util instalar tambien:

```bash
apt-get install -y curl unzip ripgrep
```

## Despliegue en un contenedor nuevo

Clonar el repositorio:

```bash
mkdir -p /opt/cgd
cd /opt/cgd
git clone https://github.com/aiprojects-pro/PPMailing.git cgd_scraper_v04
cd cgd_scraper_v04
```

Crear entorno virtual e instalar dependencias:

```bash
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt
```

Crear el fichero de configuracion local:

```bash
cp .env.example .env
chmod 600 .env
```

Editar `.env` y configurar la clave real:

```bash
export GOOGLE_PLACES_API_KEY="clave-real-de-google-places"
```

No se debe commitear `.env`. La clave debe estar restringida en Google Cloud por API y, si es posible, por IP del servidor.

Validar instalacion:

```bash
./run.sh -c "from config.segmentos import SEGMENTOS; print(f'OK: {len(SEGMENTOS)} segmentos')"
```

El resultado esperado es:

```text
OK: 8 segmentos
```

## Segmentos disponibles

Listar segmentos:

```bash
./run.sh scripts/buscar.py --listar-segmentos
```

Segmentos actualmente configurados:

- `admin_fincas`
- `clubes_deportivos`
- `empresas_servicios_deportivos`
- `campamentos_verano`
- `academias_deportivas`
- `ludotecas_ocio_infantil`
- `asesorias`
- `centros_formacion`

## Flujo completo de captacion

El flujo estandar consta de tres pasos:

1. Buscar negocios en Google Places.
2. Extraer emails desde las webs encontradas.
3. Generar CSV final con scoring.

Ejemplo para campamentos de verano en Andalucia:

```bash
./run.sh scripts/buscar.py --segmento campamentos_verano --ambito andalucia
./run.sh scripts/extraer_emails.py --input campamentos_verano_andalucia_AAAAMMDD.json
./run.sh scripts/generar_csv.py --input enriquecido_campamentos_verano_andalucia_AAAAMMDD.json
```

Tambien existe un wrapper para este piloto:

```bash
./flujo_campamentos_andalucia.sh
```

Las salidas se generan en `data/`:

```text
data/<segmento>_<ambito>_<fecha>.json
data/enriquecido_<segmento>_<ambito>_<fecha>.json
data/leads_<segmento>_<ambito>_<fecha>.csv
```

Los logs se generan en `logs/`.

## RAED

El scraper RAED permite descargar entidades deportivas del Registro Andaluz de Entidades Deportivas.

Ejemplo:

```bash
./run.sh scripts/descargar_raed.py --provincia SEVILLA --tipo "Club deportivo"
```

Tipos soportados:

- `Federacion deportiva`
- `Club deportivo`
- `Seccion deportiva`
- `Sociedad anonima deportiva`

El resultado se guarda como CSV en `data/`. Para enriquecer datos oficiales con web, telefono y email, usar posteriormente el cruce con Google Places:

```bash
./run.sh scripts/cruzar_csv.py \
  --input data/raed_sevilla_club_deportivo_AAAAMMDD.csv \
  --segmento clubes_deportivos \
  --campo-nombre nombre \
  --campo-localidad municipio \
  --campo-provincia provincia \
  --campo-id numero_inscripcion \
  --fuente-origen raed
```

## Enriquecimiento de CSV externos

Para enriquecer un CSV manual o procedente de otra fuente:

```bash
./run.sh scripts/cruzar_csv.py \
  --input data/listado_manual.csv \
  --segmento clubes_deportivos \
  --campo-nombre nombre \
  --campo-localidad localidad \
  --campo-provincia provincia \
  --fuente-origen manual
```

El CSV de entrada debe tener, como minimo, una columna de nombre. Localidad y provincia mejoran el matching.

## Variables de entorno

| Variable | Obligatoria | Uso |
|---|---:|---|
| `GOOGLE_PLACES_API_KEY` | Si | Consultas a Google Places API |

La clave debe tener habilitadas las APIs:

- Places API (New)
- Geocoding API, si se utiliza geocodificacion

## Seguridad y datos

- No subir `.env` al repositorio.
- No subir `data/` ni `logs/` si contienen leads, emails, telefonos o URLs visitadas.
- Mantener permisos restrictivos en `.env`: `chmod 600 .env`.
- Revisar cuotas y costes en Google Cloud durante ejecuciones grandes.
- Antes de importar a Mautic/Odoo, revisar y deduplicar resultados por organizacion y email.

## Operativa recomendada

Para pilotos:

```bash
./run.sh scripts/buscar.py --segmento campamentos_verano --ambito andalucia --max-paginas 1
```

Para ejecuciones completas, usar el valor por defecto de paginacion y revisar el coste estimado de Places API antes de lanzar segmentos grandes como `clubes_deportivos`.

Los segmentos con mayor volumen pueden tardar bastante porque el extractor de emails aplica un rate limit conservador contra webs externas.

## Verificacion rapida

Comprobar que la clave responde:

```bash
./run.sh - <<'PY'
import os, requests

r = requests.post(
    "https://places.googleapis.com/v1/places:searchText",
    headers={
        "Content-Type": "application/json",
        "X-Goog-Api-Key": os.environ["GOOGLE_PLACES_API_KEY"],
        "X-Goog-FieldMask": "places.displayName,places.formattedAddress",
    },
    json={"textQuery": "administrador de fincas Sevilla"},
    timeout=20,
)
print(r.status_code)
print(r.text[:500])
r.raise_for_status()
PY
```

## Mantenimiento

Actualizar codigo:

```bash
cd /opt/cgd/cgd_scraper_v04
git pull
./venv/bin/pip install -r requirements.txt
```

Limpiar salidas locales antiguas, si procede:

```bash
find data -type f -mtime +30 -delete
find logs -type f -mtime +90 -delete
```

No borrar entregables pendientes de validacion sin confirmacion funcional.
