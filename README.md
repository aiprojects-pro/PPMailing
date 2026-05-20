# PPMailing

Sistema interno de captacion B2B para generar, enriquecer y revisar leads a
partir de Google Places, webs publicas y fuentes oficiales como RAED.

La version actual incluye dos modos de uso:

- Interfaz web Flask para operativa diaria, gestion de usuarios, ejecucion de
  busquedas, descarga de CSV, leads master, programaciones y configuracion.
- Scripts CLI originales para ejecuciones manuales, mantenimiento o procesos
  batch.

Este repositorio no incluye secretos ni datos generados. Los ficheros `.env`,
`data/`, `logs/` y `webui/instance/` deben existir solo en el servidor.

## Estado de despliegue

- Runtime: Python 3.10 o superior
- Framework web: Flask + Gunicorn
- Proxy recomendado: Nginx con Let's Encrypt
- Persistencia web: SQLite en `webui/instance/ppmailing.db`
- Cola de trabajos: interna al proceso web, por eso Gunicorn debe usar `-w 1`
- Salidas CSV/JSON: `data/` y `webui/instance/job_outputs/`
- Tests incluidos: pytest

## Estructura

```text
.
├── config/                 # Segmentos, ciudades y configuracion base
├── core/                   # Places, parsing, scoring, RAED y extraccion email
├── docs/                   # Guias tecnicas de Google Cloud y RAED
├── scripts/                # Entradas CLI
├── webui/                  # Aplicacion Flask
├── tests/                  # Suite de tests de la interfaz web
├── plantillas_email/       # Plantillas comerciales por segmento
├── data/                   # Datos generados; no versionado
├── logs/                   # Logs locales; no versionado
├── requirements.txt
├── run.sh
└── .env.example
```

## Instalacion en Debian

Paquetes base:

```bash
apt-get update
apt-get install -y python3 python3-venv python3-pip git curl unzip ca-certificates
```

Clonar el repositorio:

```bash
mkdir -p /opt/cgd
cd /opt/cgd
git clone https://github.com/aiprojects-pro/PPMailing.git cgd_scraper_v04
cd cgd_scraper_v04
```

Crear entorno Python:

```bash
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt
```

Crear directorios privados:

```bash
mkdir -p data logs webui/instance
chmod 700 webui/instance
```

Configurar variables locales:

```bash
cp .env.example .env
chmod 600 .env
```

Editar `.env` y definir como minimo:

```bash
GOOGLE_PLACES_API_KEY=clave-real-de-google-places
```

La clave de Google debe estar restringida en Google Cloud por API y, si es
posible, por IP del servidor.

## Arranque manual

Para probar en local:

```bash
PPM_HOST=127.0.0.1 \
PPM_PORT=5000 \
PPM_DEBUG=0 \
PPM_ADMIN_PASSWORD='cambiar-esta-password' \
./venv/bin/python -m webui.app
```

La primera ejecucion crea el usuario `admin`. La variable
`PPM_ADMIN_PASSWORD` solo se usa si la base de datos aun no contiene usuarios.

## Despliegue con systemd

Crear `/etc/ppmailing/ppmailing.env`:

```bash
mkdir -p /etc/ppmailing
chmod 700 /etc/ppmailing
cat >/etc/ppmailing/ppmailing.env <<'EOF'
PPM_PROXIED=1
PPM_HOST=127.0.0.1
PPM_PORT=5000
PPM_DEBUG=0
PPM_ADMIN_PASSWORD=cambiar-esta-password
GOOGLE_PLACES_API_KEY=clave-real-de-google-places
EOF
chmod 600 /etc/ppmailing/ppmailing.env
```

Crear `/etc/systemd/system/ppmailing.service`:

```ini
[Unit]
Description=PPMailing web interface
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/cgd/cgd_scraper_v04
EnvironmentFile=/etc/ppmailing/ppmailing.env
ExecStart=/opt/cgd/cgd_scraper_v04/venv/bin/gunicorn -w 1 -b 127.0.0.1:5000 'webui.app:create_app()'
Restart=always
RestartSec=5
User=root
Group=root

[Install]
WantedBy=multi-user.target
```

Activar:

```bash
systemctl daemon-reload
systemctl enable --now ppmailing
systemctl status ppmailing --no-pager
```

## Nginx y Let's Encrypt

Instalar:

```bash
apt-get install -y nginx certbot python3-certbot-nginx
```

Virtual host de ejemplo para `mailing.aiprojects.pro`:

```nginx
server {
    server_name mailing.aiprojects.pro;
    client_max_body_size 2m;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $host;
        proxy_read_timeout 300;
    }
}
```

Activar SSL:

```bash
nginx -t
systemctl reload nginx
certbot --nginx -d mailing.aiprojects.pro --redirect
```

Comprobar:

```bash
curl -I http://mailing.aiprojects.pro/
curl -I https://mailing.aiprojects.pro/login
```

## Uso web

Entrar en la URL publicada y acceder con el usuario administrador inicial.
Tras el primer login, cambiar la password desde `Mi cuenta`.

La interfaz permite:

- lanzar busquedas por segmento y ambito;
- ejecutar segmentos como `campamentos_verano`, `clubes_deportivos` o
  `centros_formacion`;
- descargar CSV de cada job finalizado;
- revisar leads deduplicados en la tabla master;
- configurar usuarios, presupuesto y clave de Google Places;
- programar busquedas recurrentes;
- configurar Mailgun para validacion/envio cuando proceda.

## Uso CLI

Listar segmentos:

```bash
./run.sh scripts/buscar.py --listar-segmentos
```

Ejecutar campamentos de verano en Andalucia:

```bash
./run.sh scripts/buscar.py --segmento campamentos_verano --ambito andalucia
./run.sh scripts/extraer_emails.py --input campamentos_verano_andalucia_AAAAMMDD.json
./run.sh scripts/generar_csv.py --input enriquecido_campamentos_verano_andalucia_AAAAMMDD.json
```

Ejecutar RAED para clubes deportivos:

```bash
./run.sh scripts/descargar_raed.py --provincia SEVILLA --tipo "Club deportivo"
```

Cruzar un CSV RAED con Google Places:

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

## Segmentos incluidos

- `admin_fincas`
- `clubes_deportivos`
- `empresas_servicios_deportivos`
- `campamentos_verano`
- `academias_deportivas`
- `ludotecas_ocio_infantil`
- `asesorias`
- `centros_formacion`

## Validacion

Instalar dependencias de test:

```bash
./venv/bin/pip install pytest
```

Ejecutar:

```bash
./venv/bin/python -m pytest tests/
```

## Seguridad operativa

- No subir `.env`, `/etc/ppmailing/ppmailing.env`, `data/`, `logs/` ni
  `webui/instance/`.
- Mantener permisos `600` en ficheros con claves y `700` en directorios
  privados.
- Usar siempre HTTPS en produccion.
- Limitar cuotas y alertas de Google Cloud antes de ejecuciones amplias.
- Revisar los CSV antes de integrarlos con Mautic/Odoo.
- Mantener Gunicorn con un solo worker mientras la cola siga siendo interna.

## Mantenimiento

Logs del servicio:

```bash
journalctl -u ppmailing -f
```

Reiniciar:

```bash
systemctl restart ppmailing
```

Renovar certificados:

```bash
certbot renew --dry-run
```

Backup minimo antes de actualizar:

```bash
tar -czf /opt/cgd/backups/ppmailing_$(date +%Y%m%d_%H%M%S).tar.gz \
  --exclude venv \
  --exclude .git \
  /opt/cgd/cgd_scraper_v04
```
