# Activación de Google Places API — Guía paso a paso

Este documento se entrega a los administradores de sistemas de CGD para que
preparen el acceso a Google Places API, necesario para el scraper de
despachos de administradores de fincas (Fase 0 — pivote B2B).

Tiempo estimado: **10-15 minutos**.

---

## Paso 1 — Cuenta de Google Cloud

1. Ir a https://console.cloud.google.com
2. Iniciar sesión con una cuenta corporativa de CGD (recomendado: no usar una cuenta personal).
3. Si es la primera vez, aceptar términos.

## Paso 2 — Crear un proyecto

1. Botón superior izquierdo → "Seleccionar proyecto" → "Nuevo proyecto".
2. Nombre del proyecto: `cgd-captacion-b2b`
3. Organización: la que aplique en CGD (o sin organización si es cuenta personal corporativa).
4. Crear.

## Paso 3 — Activar facturación

Esto es obligatorio aunque el uso quede dentro del crédito gratuito mensual de 200 USD.

1. Menú lateral → "Facturación" → "Vincular cuenta de facturación".
2. Crear una cuenta de facturación nueva.
3. Asociar una tarjeta de la empresa.
4. Importante: configurar **alerta de presupuesto a 50 USD** para evitar sobrecostes accidentales.

## Paso 4 — Activar la Places API

1. Menú lateral → "APIs y servicios" → "Biblioteca".
2. Buscar **"Places API (New)"** (la versión nueva, no la legacy).
3. Pulsar "Habilitar".
4. Repetir para **"Geocoding API"** (la usamos para convertir ciudades en coordenadas).

## Paso 5 — Crear la clave API

1. Menú lateral → "APIs y servicios" → "Credenciales".
2. Pulsar "Crear credenciales" → "Clave de API".
3. Se mostrará la clave: copiarla y guardarla en un sitio seguro (gestor de secretos
   interno de CGD, vault, o archivo .env restringido al usuario que ejecutará el scraper).
4. Pulsar "Restringir clave":
   - **Restricciones de API**: marcar solo "Places API (New)" y "Geocoding API".
   - **Restricciones de aplicación**: "Direcciones IP" → añadir la IP fija del servidor
     que ejecutará el scraper. Si no hay IP fija, dejar sin restricción y blindar la
     clave por otros medios (rotación periódica, alerta de uso anómalo).
5. Guardar.

## Paso 6 — Configurar alerta de cuota

1. Menú lateral → "APIs y servicios" → "Cuotas y límites del sistema".
2. Buscar Places API → "Text Search requests per day" y "Place Details requests per day".
3. Establecer límite diario: **2.000 peticiones/día** (margen amplio para el piloto,
   muy por debajo del límite que generaría cobro).

## Paso 7 — Entrega segura de la clave

Una vez tengáis la clave, hacerla llegar al responsable del proyecto **a través del
canal seguro habitual en CGD** (no por chat, no por email sin cifrar).

La clave NO va dentro del código fuente. Se inyecta en producción mediante variable
de entorno `GOOGLE_PLACES_API_KEY` o archivo `.env` con permisos 600 (legible solo
por el usuario que ejecuta el scraper).

---

## Costes esperados (Fase 0 — Andalucía)

| Concepto | Cantidad estimada | Coste unitario | Total |
|---|---|---|---|
| Búsquedas Text Search (8 provincias × 5 variantes) | ~40 | $0,032 / búsqueda | ~$1,30 |
| Detalles de cada lugar (≈ 2.500 despachos × 1) | ~2.500 | $0,017 / consulta | ~$42,50 |
| Geocoding (8 ciudades) | 8 | $0,005 / consulta | $0,04 |
| **TOTAL** | | | **≈ 44 USD** |

Todo cae dentro del crédito gratuito mensual de 200 USD. **Coste real esperado: 0 €.**

Para futuras fases que escalen a toda España, el coste estimado es de 150-180 USD, también
dentro del crédito gratuito. Si se superan los 200 USD/mes habría que valorar arquitectura
(p. ej. caché de resultados, frecuencia trimestral en lugar de mensual).

---

## Validación rápida una vez creada la clave

Desde una terminal con `curl` instalado, sustituir `TU_CLAVE` y ejecutar:

```bash
curl -X POST 'https://places.googleapis.com/v1/places:searchText' \
  -H 'Content-Type: application/json' \
  -H 'X-Goog-Api-Key: TU_CLAVE' \
  -H 'X-Goog-FieldMask: places.displayName,places.formattedAddress' \
  -d '{"textQuery": "administrador de fincas Sevilla"}'
```

Si todo está bien, devolverá un JSON con varios resultados. Si devuelve error 403
"API key not valid" o similar, revisar las restricciones del paso 5.
