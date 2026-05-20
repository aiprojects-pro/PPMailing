# PPMailing — Interfaz web (v2)

Wrapper Flask sobre los scripts CLI existentes. No modifica el código de
`core/`, `config/` ni `scripts/`: los invoca como subprocesos.

Esta es la versión 2, refactorizada en módulos tras una auditoría de
seguridad. Cubre todos los hallazgos críticos, altos y medios identificados.

## Características

- **Login con roles** (`admin` / `user`) con cierre de sesión seguro
- **Lanzar los 8 segmentos preconfigurados** + crear nuevos desde la UI
- **Pipeline completo en background**: buscar → extraer emails → generar CSV
- **Búsqueda por ciudad** (subconjuntos predefinidos) **o por radio
  geográfico** (hasta 20 puntos con lat/lng/radio personalizado)
- **Búsquedas programadas recurrentes**: modo simple (diario/semanal/mensual)
  o cron Unix (`0 6 * * 1` = lunes a las 6am). Respeta el presupuesto del
  usuario y se auto-desactiva tras 5 fallos seguidos
- **Estimación de coste antes de lanzar** (evita facturas grandes inesperadas)
- **Presupuesto mensual por usuario** (opcional; lo configura el admin)
- **Cola de jobs serializada**: nunca corren dos a la vez, lo que respeta
  los rate-limits de Google Places y de las webs scrapeadas
- **Outputs aislados por job** (sin colisiones de nombres en `data/`)
- **Base de datos central de leads** (`leads_master`): cada lead aparece UNA
  vez aunque salga en varias búsquedas; estado (`nuevo`/`contactado`/
  `respondió`/`descartado`), notas, histórico
- **Validación de emails con 3 métodos**: MX (gratis, DNS), SMTP handshake
  (gratis, arriesgado), Mailgun Email Validation API (de pago, fiable).
  Toggle por validación, individual o en lote
- **Extracción de redes sociales** desde la web del negocio: LinkedIn,
  Instagram, Facebook, Twitter/X, YouTube, TikTok (manual o en lote)
- **Integración bidireccional con Mailgun**: push de leads como mailing
  list + webhook receiver con verificación HMAC. Los eventos
  `delivered`/`opened`/`clicked` marcan el lead como `contactado`;
  `complained`/`unsubscribed` lo marcan como `descartado`
- **Panel admin** para usuarios, presupuestos, clave de Google Places,
  configuración de Mailgun, política de retención y auditoría de logins
- **Protección CSRF** en todos los formularios
- **Rate-limit en login** (10/minuto) contra fuerza bruta
- **Headers de seguridad HTTP** + cookies hardened
- **Suite de tests con pytest** (160 tests)

## Estructura

```
webui/
├── app.py              # Application factory; orquesta blueprints
├── paths.py            # Constantes de rutas (BD, logs, outputs)
├── db.py               # Esquema SQLite + helpers de conexión
├── auth.py             # Blueprint /login, /logout, /account
├── admin.py            # Blueprint /admin/* (CRUD usuarios, settings)
├── segments.py         # Blueprint /segments/* (CRUD segmentos custom)
├── jobs.py             # Blueprint /jobs/*: cola, worker, descargas
├── dashboard.py        # Blueprint /: landing con estimador de coste
├── security.py         # CSRF, rate-limiter, headers de seguridad
├── settings.py         # API key con escritura atómica
├── cost.py             # Estimador de coste de Places API
├── run_with_extras.py  # Wrapper para inyectar segmentos custom
├── templates/          # Plantillas Jinja2
├── static/css/         # Estilos
└── instance/           # Datos en runtime (NO en git)
    ├── ppmailing.db
    ├── settings.json
    ├── .flask_secret
    ├── job_logs/
    ├── job_outputs/<job_id>/
    └── extra_segments/

tests/
├── conftest.py         # Fixtures (app, clientes)
├── test_auth.py        # 14 tests de login/sesiones
├── test_segments.py    # 9 tests, incluye regresión race condition
├── test_jobs.py        # 11 tests, incluye autorización entre usuarios
├── test_admin.py       # 10 tests del panel admin
└── test_security.py    # 8 tests de CSRF, headers, rate-limit
```

## Instalación

```bash
./venv/bin/pip install flask werkzeug Flask-WTF Flask-Limiter croniter
```

Dependencias añadidas en la v2:
- `Flask-WTF` — protección CSRF
- `Flask-Limiter` — rate-limiting en login
- `croniter` — parsing de expresiones cron para búsquedas programadas

## Arranque

### Desarrollo (acceso local)

```bash
./venv/bin/python -m webui.app
# Disponible en http://127.0.0.1:5000
```

La primera vez se crea automáticamente un usuario `admin` con contraseña
`admin`. **Cámbiala lo antes posible** desde el menú "Mi cuenta".

### Producción tras reverse proxy (Caddy / Nginx con HTTPS)

```bash
export PPM_PROXIED=1
export PPM_HOST=127.0.0.1
export PPM_PORT=5000
./venv/bin/python -m webui.app
```

Con `PPM_PROXIED=1`:
- Se activa `SESSION_COOKIE_SECURE=True` (cookies solo por HTTPS).
- Se aplica `ProxyFix` para respetar los headers `X-Forwarded-*` del proxy.

Ejemplo de configuración Caddy:

```
ppmailing.tudominio.com {
    reverse_proxy 127.0.0.1:5000
}
```

### Con gunicorn (recomendado para producción)

```bash
./venv/bin/pip install gunicorn
PPM_PROXIED=1 ./venv/bin/gunicorn -w 1 -b 127.0.0.1:5000 'webui.app:create_app()'
```

> **Importante**: usa `-w 1` (un solo worker). La cola de jobs es interna al
> proceso. Con más workers, cada uno tendría su propia cola y los jobs no se
> serializarían. Si necesitas más concurrencia, usa Celery o RQ con Redis.

## Variables de entorno

| Variable | Por defecto | Descripción |
|---|---|---|
| `PPM_HOST` | `127.0.0.1` | Host de escucha |
| `PPM_PORT` | `5000` | Puerto |
| `PPM_DEBUG` | `0` | `1` para activar debug (NO usar en producción) |
| `PPM_PROXIED` | `0` | `1` si estás tras reverse proxy HTTPS |
| `PPM_ADMIN_PASSWORD` | `admin` | Solo se usa en la **primera** ejecución |
| `GOOGLE_PLACES_API_KEY` | — | Fallback si no se configura desde la UI |

## Tests

```bash
./venv/bin/pip install pytest
./venv/bin/python -m pytest tests/
```

Los tests usan un `instance/` temporal por test (aislado en `/tmp`), así
que no tocan tu base de datos real. Tardan ~15 segundos.

## Notas de seguridad

- **CSRF**: todos los POST llevan token. Si te llega un 400 al enviar un
  formulario, probablemente la sesión expiró (token caduca a las 4h).
- **Sesiones**: duran 8h. Al cambiar la contraseña se invalidan las
  sesiones abiertas en otros navegadores (vía `session_version`).
- **Rate-limit**: 10 intentos de login por minuto / 30 por hora desde
  una misma IP. Al rebasarlo se devuelve 429 (con plantilla bonita).
- **API key**: se almacena en `webui/instance/settings.json` con
  permisos `600`. En la UI siempre se muestra enmascarada (primeros 6 +
  últimos 4 caracteres).
- **Datos de jobs**: cada job tiene su propia carpeta en
  `webui/instance/job_outputs/<id>/`. Esto evita que dos búsquedas del
  mismo segmento+ámbito en el mismo día se pisen los archivos.
- **Recovery de jobs huérfanos**: al arrancar, cualquier job que estuviese
  en estado `running` o `pending` se marca como error con mensaje
  "interrumpido por reinicio del servidor".

## Modos de búsqueda

El dashboard ofrece dos tabs para lanzar una búsqueda manual:

### Por ciudad (subconjunto)

El modo original: la búsqueda se ejecuta sobre uno de los subconjuntos
predefinidos en `config/ciudades_espana.py` (`espana`, `andalucia`,
`madrid`, `barcelona`, `capitales`). Para cada ciudad del subconjunto se
ejecuta cada query del segmento.

Coste: `nº ciudades × nº queries × páginas`.

### Por radio geográfico

Para barrer áreas concretas que el modo "ciudad" no cubre bien (barrios
periféricos de Madrid, polígonos industriales, etc.). Defines de 1 a 20
puntos. Cada uno tiene:

- **lat / lng** — coordenadas del centro del círculo.
- **radio** — entre 100 m y 50 km (límite de Google Places).
- **etiqueta** (opcional) — para identificar el punto en logs.

Tip: en Google Maps, clic derecho sobre un punto te da las coordenadas
exactas para copiar y pegar.

Coste: `nº puntos × nº queries × páginas`. Igual de barato por punto que
una ciudad, pero los resultados están concentrados en el área que pides.

**Importante**: la búsqueda por radio se ejecuta in-process (Python),
no como subproceso. Eso evita duplicar código y nos da mejor reporte de
progreso. El módulo `webui/places_radius.py` reutiliza
`core.places_client` del proyecto base sin modificarlo.

## Búsquedas programadas (`/schedules`)

Si necesitas lanzar la misma búsqueda periódicamente (mercado en
movimiento, captación continua), puedes programarla:

### Modo simple

Tres frecuencias preestablecidas: diaria, semanal o mensual.

### Modo avanzado (cron)

Expresión cron Unix estándar:

| Expresión | Significado |
|---|---|
| `0 6 * * 1` | lunes a las 6:00 |
| `30 8 1 * *` | día 1 de cada mes a las 8:30 |
| `0 9 * * 1-5` | de lunes a viernes a las 9:00 |

**Mínimo absoluto: 1 hora entre ejecuciones.** Aunque la expresión cron
permita menos (`* * * * *` = cada minuto), el worker lo ajusta para no
quemar la cuota de API.

### Garantías de seguridad y robustez

- **Presupuesto del usuario**: la programación se ejecuta como el usuario
  que la creó. Si su presupuesto mensual no llega a cubrir el coste
  estimado, se salta esa ejecución (sin contar como fallo) y se
  reagenda para la siguiente fecha.
- **Auto-desactivación**: tras 5 fallos consecutivos (errores reales,
  no presupuesto agotado), la programación se desactiva sola con un
  mensaje en `last_error`. Reactivarla resetea el contador.
- **Permisos**: cada usuario solo ve y modifica sus propias programaciones.
  El admin ve y modifica todas.
- **Eliminación en cascada**: si se borra el usuario que creó la
  programación, la programación se borra automáticamente.
- **Worker resiliente**: cada ciclo del scheduler tiene su propio
  try/except. Un error en una programación no afecta a las demás.

### Ejecución desde el worker

El worker `ppmailing-scheduler-worker` se ejecuta como daemon thread y
hace polling cada 60 segundos. Al detectar una programación con
`next_run_at <= now`:

1. Comprueba que el usuario y el segmento existen.
2. Valida el coste contra el presupuesto del usuario.
3. Crea el job en BD (de tipo `subset` o `radius` según corresponda)
   y lo encola en el worker de jobs.
4. Calcula el siguiente `next_run_at` y lo guarda.

## Extracción de redes sociales

Cada lead puede tener URLs de hasta 6 redes sociales del negocio:
LinkedIn, Instagram, Facebook, Twitter/X, YouTube, TikTok.

### Desde el detalle del lead

Botón "Extraer redes ahora" en la vista `/leads/<place_id>`. Descarga
la web del lead y parsea las URLs encontradas. Si la web no responde
o no hay URLs, los campos quedan vacíos.

### En lote

Desde el listado `/leads`, botón "Extraer redes sociales". Procesa hasta
200 leads del filtro actual que aún no tengan `social_extracted_at`. Es
una operación lenta (descarga una web por lead), pero solo se hace una
vez por lead.

### Filtros aplicados

El módulo `webui/social_extraction.py` aplica reglas para no capturar
URLs que NO representan al negocio:

- URLs de share / intent (botones genéricos "compartir en X")
- URLs a posts individuales (`/p/...`, `/reel/...`, `/watch?v=...`)
- URLs de búsqueda u oficiales de la plataforma

Si hay varias URLs candidatas de la misma red, se elige la más corta
(suele ser la página principal, sin sub-paths como `/posts/123`).

## Validación profunda de emails (SMTP / Mailgun)

Además de la validación MX (DNS) gratuita y rápida, hay dos métodos
profundos para casos en los que el bounce sería caro:

### SMTP handshake (gratis, arriesgado)

Conecta al MX server del dominio y hace HELO + MAIL FROM + RCPT TO.
Devuelve `verified` / `invalid` / `unknown`.

**Atención:** muchos proveedores grandes (Gmail, Outlook) grey-listan
o bloquean handshakes sin un MTA real detrás. Usar con prudencia.

### Mailgun Email Validation API (de pago, fiable)

Requiere haber configurado Mailgun en el panel admin. Llama a
`/v4/address/validate` y mapea los resultados:

| Mailgun result   | Estado en BD |
|------------------|--------------|
| `deliverable`    | `verified`   |
| `undeliverable`  | `invalid`    |
| `do_not_send`    | `do_not_send` (blacklist, role, spamtrap) |
| `catch_all`      | `catch_all` (el dominio acepta TODO) |
| `unknown`        | `unknown`    |

Coste aproximado: **$0.005 por email**.

### Selector en la UI

Tanto en el detalle del lead como en validación en lote, el formulario
incluye un selector con los tres métodos. Recomendado:

- **MX** para limpieza masiva inicial (descarta dominios muertos)
- **Mailgun** para validar la lista final antes de enviar campaña
- **SMTP** solo para verificación manual puntual

## Integración con Mailgun (bidireccional)

### Configuración (admin)

En el panel admin, sección "Integración con Mailgun":

- **API key**: la principal de tu cuenta Mailgun
- **Dominio**: `mg.tudominio.com`
- **Base URL**: `https://api.mailgun.net` (US) o `https://api.eu.mailgun.net` (EU)
- **Webhook Signing Key**: distinta de la API key. La encuentras en
  Mailgun → Sending → Webhooks. **Sin ella, los webhooks se rechazan.**

### Push de leads

Desde `/leads`, botón "Push a Mailgun". Te lleva a `/mailgun/push` con
los filtros pre-rellenos. Eliges la mailing list de destino y se hace
upsert de los miembros. Cada lead lleva como vars: `place_id`,
`segmento`, `localidad`, `score` — accesibles en tus plantillas Mailgun
con `%recipient.place_id%`, etc.

Recomendado **siempre** marcar "Excluir ya contactados" para no
reenviar a leads ya tocados.

### Webhook receiver

Endpoint público (sin login, pero firmado): `POST /webhooks/mailgun`

Configura este URL en Mailgun (Sending → Webhooks) para los eventos
que quieras reflejar en el estado del lead:

| Evento Mailgun  | Acción en BD                                     |
|-----------------|--------------------------------------------------|
| `delivered`     | lead → `contactado` (si estaba `nuevo`)          |
| `opened`        | lead → `contactado` (si estaba `nuevo`)          |
| `clicked`       | lead → `contactado` (si estaba `nuevo`)          |
| `complained`    | lead → `descartado`                              |
| `unsubscribed`  | lead → `descartado`                              |
| `failed`        | solo añade nota (no cambia estado)               |
| `rejected`      | solo añade nota                                  |

Cada evento añade una nota al lead con timestamp:
`[mailgun delivered @ 2026-05-19T19:09:02]`.

### Seguridad del webhook

**Verificación HMAC obligatoria.** Cada webhook viene firmado con
HMAC-SHA256 usando la `webhook_signing_key`. Si la firma no coincide o
no hay signing_key configurada, devolvemos 401. Esto impide que
cualquiera envíe eventos falsos para marcar leads como descartados.

## Sistema de leads

Cada búsqueda termina volcando su CSV a una tabla central llamada
`leads_master`. Es la pieza que convierte la app en algo más que un
generador de CSVs: te da un histórico unificado y deduplicado de todos
los negocios encontrados.

### Deduplicación

La clave primaria de `leads_master` es el `place_id` de Google. Si lanzas
dos búsquedas que devuelven el mismo negocio (típico al solapar segmentos
o ámbitos):

- El registro **NO se duplica**.
- Los datos se actualizan con los más recientes (rating, score, etc.).
- El contador `times_seen` se incrementa.
- La relación `lead_jobs` registra los IDs de los jobs donde apareció.

Esto evita el problema clásico: contactar al mismo negocio tres veces
porque salió en tres CSVs distintos.

### Estados de lead

Cada lead tiene un estado:

- `nuevo` (default): recién extraído, sin acción.
- `contactado`: se le envió outreach. Se registra automáticamente la
  fecha en `fecha_ultimo_contacto`.
- `respondio`: ha respondido al outreach.
- `descartado`: no apto para campaña (decisión manual).

Cada lead admite además **notas internas** libres. El estado y las notas
se conservan aunque el lead vuelva a aparecer en búsquedas posteriores
(la re-ingesta no resetea estos campos).

### Validación de emails (MX)

Desde el detalle de un lead, botón "Validar email ahora": comprueba si
el dominio tiene registros MX (o, en su defecto, A) usando `nslookup`.
Resultado:

- `mx_ok`: el dominio puede recibir correo.
- `mx_fail`: dominio muerto, error de tipo o sintaxis inválida.

También hay validación en lote desde el listado: "Validar emails sin
verificar" procesa hasta 500 leads del filtro actual de una sola vez.
Cada validación es ~50ms; no requiere coste externo, solo DNS.

### Vista de leads (`/leads`)

Filtros disponibles:
- Búsqueda libre por nombre, email o localidad.
- Por segmento.
- Por estado.
- Por validación de email.
- Sólo aptos para campaña.
- Ordenación por score, fecha vista, nombre.

### Exportación

Desde el listado, botón "Exportar CSV" con opciones:
- **Excluir ya contactados** (`contactado` + `respondio`): evita reenviar
  outreach a leads ya tocados.
- **Solo emails con MX OK**: filtra de raíz los dominios muertos.

El CSV exportado incluye todos los campos del lead: contacto, score,
estado, fecha de último contacto, notas, etc.

## Limpieza de archivos antiguos

La interfaz incluye **dos mecanismos** para limpiar búsquedas antiguas:

### Borrado manual

Cada búsqueda terminada tiene un botón "Eliminar" en su tabla del dashboard
y otro en su página de detalle. Al hacer clic:

- Se borra la fila de la BD.
- Se borra el log de ejecución.
- Se borra la carpeta `instance/job_outputs/<id>/` con todos sus archivos.
- Es **permanente, no hay papelera**.

Permisos: el dueño del job y los admins pueden borrar. Los jobs en estado
`running` o `pending` no se pueden borrar (hay que cancelarlos o esperar).

### Retención automática

En el panel admin (sección "Retención de búsquedas") puedes activar una
política de borrado por antigüedad:

- **Por defecto: desactivada**. Las búsquedas se conservan indefinidamente.
- Si la activas y configuras (p. ej.) "conservar durante 90 días", un worker
  interno cada hora borra las búsquedas terminadas más antiguas de ese
  periodo, junto con sus archivos y logs.
- **No afecta a jobs en cola o en ejecución**, sólo a los que están en
  estados `done`, `error` o `cancelled`.

Si prefieres no depender del worker interno, puedes desactivar la retención
en la UI y usar cron en su lugar:

```cron
0 4 * * * find /ruta/al/proyecto/data -type f -mtime +30 -delete
```

Pero la limpieza interna respeta los archivos por job_id, mientras que el
cron sobre `data/` borra archivos comunes (puede afectar a varios jobs a
la vez), así que **la interna es preferible**.

## Cambios respecto a la v1

Resumen de hallazgos de la auditoría y dónde se resolvieron:

| ID | Hallazgo | Solución |
|---|---|---|
| C-1 | Sin CSRF en POSTs | `security.py`: `CSRFProtect` + token en todos los forms |
| C-2 | Race en `user_segments.json` | Segmentos custom movidos a SQLite (`segments` table) |
| C-3 | Sin estimación de coste | `cost.py` + endpoint `/jobs/estimate` + confirmación si ≥20€ |
| H-1 | Jobs concurrentes saturan Google | Cola FIFO con un único worker (`jobs.py`) |
| H-2 | Colisión de archivos por fecha | Outputs aislados en `instance/job_outputs/<id>/` |
| H-3 | Sin headers de seguridad | `security.py`: CSP, X-Frame-Options, HSTS, etc. |
| M-1 | Cookies sin Secure/SameSite | `app.py`: `SESSION_COOKIE_SAMESITE='Lax'`, `Secure` con `PPM_PROXIED` |
| M-2 | Cambio password no invalida sesiones | Columna `session_version` en `users`, comparada en cada request |
| M-3 | Sin rate-limit en login | `Flask-Limiter` con `10/minute; 30/hour` |
| M-4 | Falta de tests | `tests/` con pytest, 52 tests |
| M-5 | Jobs huérfanos tras reinicio | `recover_orphan_jobs()` al arrancar |
| L-1 | Logout era GET | Ahora es POST con CSRF |
| L-2 | Username permisivo | Regex `[a-z0-9][a-z0-9_.-]{1,31}` + normalización a minúsculas |
| L-5 | Race en init_db | `INSERT OR IGNORE` en lugar de SELECT-then-INSERT |
| L-6 | Path traversal teórico | Nombre del extras file basado en `uuid`, no en segmento_id |
| L-7 | Sin CHECK constraint en role | `CHECK (role IN ('user','admin'))` |
| L-8 | csv_path absoluto en BD | Ahora se guarda solo el filename + dir del job |
