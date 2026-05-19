# Captura del comportamiento del buscador RAED — Guía DevTools

**Para:** Administradores de sistemas de CGD
**Tiempo estimado:** 10-15 minutos
**Objetivo:** Capturar exactamente cómo funciona internamente el formulario de búsqueda del Registro Andaluz de Entidades Deportivas (RAED), para que se pueda programar el scraper correspondiente.

## Por qué hace falta esto

El buscador del RAED es un formulario que envía una petición HTTP **POST** al servidor cuando se pulsa "Buscar". Necesitamos saber exactamente:

- A qué URL envía la petición.
- Qué cabeceras (headers) lleva.
- Qué datos en el cuerpo (body).
- Si requiere algún token CSRF u otra protección.
- Qué formato tiene la respuesta HTML con los resultados.

Sin esta información, programar el scraper sería ensayo y error con muchas iteraciones. Con ella, está hecho en 1-2 horas.

## Procedimiento

### 1. Abrir Chrome (o Edge, Firefox; las instrucciones son similares)

Cualquier navegador moderno vale. Usaremos Chrome como ejemplo.

### 2. Abrir DevTools antes de ir a la página

- Windows / Linux: pulsar `F12` o `Ctrl + Shift + I`
- Mac: pulsar `Cmd + Option + I`

Aparecerá un panel lateral o inferior. Lo dejamos abierto.

### 3. Activar la pestaña "Network" (Red)

Dentro de DevTools, hay varias pestañas en la parte superior: Elements, Console, **Network**, Sources… Pulsar en **Network**.

Importante:
- Verificar que el botón rojo de "grabación" está activo (es un círculo, debe estar rojo). Si está gris, pulsarlo.
- Marcar la casilla **"Preserve log"** (preservar el registro). Esto evita que se borren las peticiones al cambiar de página.

### 4. Navegar al buscador del RAED

En la barra de direcciones, escribir:

```
https://www.juntadeandalucia.es/deporte/dpweb/buscadorRaed/index
```

Pulsar Enter. La página cargará. En el panel Network verás muchas peticiones HTTP (HTML, CSS, JS, imágenes…). Eso es normal.

### 5. Hacer una búsqueda de prueba

En el formulario:
- **Tipos de Entidad**: seleccionar "Club deportivo"
- **Provincia**: seleccionar "SEVILLA"

(No hace falta nada más. Cuantos menos filtros, mejor para entender el formato general).

Pulsar el botón **"Buscar"**.

### 6. Localizar la petición POST en Network

En el panel Network aparecerá una nueva petición justo cuando pulsas "Buscar". Buscamos la que cumpla estas dos condiciones:

- **Method**: POST (no GET)
- **Type / Initiator**: documento HTML (no css, no imagen, no script)

Suele aparecer destacada. El "Name" probablemente sea algo como `buscar`, `buscadorRaed/buscar`, o similar.

Hacer **clic sobre esa petición**. Se abrirá un panel a la derecha con varias subpestañas: Headers, Payload, Response…

### 7. Capturar los datos clave

Necesitamos **cuatro cosas** que te vamos a pedir copiar y enviar al responsable del proyecto:

#### a) URL completa (subpestaña Headers)

En la parte superior del panel pone:
- Request URL: `https://www.juntadeandalucia.es/deporte/dpweb/...`

**Copiar la URL completa.**

#### b) Headers de la petición (Request Headers)

Bajar en la subpestaña Headers hasta encontrar "Request Headers". Hay una lista de cabeceras (User-Agent, Cookie, Referer, etc.).

Hacer clic en **"View source"** (a la derecha de "Request Headers") si está disponible para verlas en formato raw.

**Copiar todas las Request Headers tal cual.** Especialmente importantes:
- `Cookie:`
- `Referer:`
- `X-CSRF-Token:` (si existe)
- `Content-Type:`

#### c) Cuerpo de la petición (subpestaña Payload)

En la subpestaña **Payload** verás los datos que se envían al servidor. Suele haber dos vistas:
- **Form Data** (legible)
- **view source** (raw, formato `key1=valor1&key2=valor2`)

**Copiar la vista "view source"** del Form Data. Es la versión exacta que se envía.

#### d) Respuesta HTML (subpestaña Response)

En la subpestaña **Response** se ve el HTML que devuelve el servidor con los resultados de la búsqueda.

Hacer **clic derecho dentro de la subpestaña Response → "Copy" → "Copy response"** (o seleccionar todo y copiar). Si el HTML es muy largo, también vale con copiar los primeros 200 KB.

Pegar todo en un archivo de texto.

### 8. Bonus: navegar a la página 2

Si los resultados aparecen paginados (típicamente 10 por página), repetir el procedimiento haciendo clic en "Siguiente página":

- Volver al panel Network.
- Identificar la nueva petición POST que se dispara.
- Capturar de nuevo URL, Headers, Payload, Response.

Esto nos permite ver **cómo se hace la paginación** (a veces es un parámetro `page=2`, otras un token).

### 9. Bonus: ver el detalle de un club

Hacer clic en uno de los clubes del listado para entrar en su ficha individual. Capturar:

- URL de la ficha (a menudo es GET con el ID del club).
- HTML de la respuesta.

Esto nos da el formato de los datos detallados (web, teléfono, etc. — si es que están).

## Qué enviar

Crear un archivo de texto (puede llamarse `captura_raed.txt`) con esta estructura:

```
==== BÚSQUEDA INICIAL ====

URL:
[pega aquí la URL completa de la petición POST]

REQUEST HEADERS:
[pega aquí todas las Request Headers]

PAYLOAD (view source):
[pega aquí el cuerpo de la petición en formato raw]

RESPONSE (HTML):
[pega aquí el HTML de respuesta]


==== PÁGINA 2 (si aplica) ====

URL:
[...]

PAYLOAD:
[...]


==== DETALLE DE UN CLUB ====

URL:
[...]

RESPONSE:
[...]
```

Enviar este archivo al responsable del proyecto. Con esto programaremos el scraper en 1-2 horas y será robusto a la primera.

## Si algo no encaja con estas instrucciones

El buscador puede haber cambiado desde la fecha de redacción de esta guía. Si:

- No aparece una petición POST clara cuando pulsas "Buscar" → probablemente sea una SPA (JavaScript que hace fetch a una API JSON). En ese caso, busca peticiones de tipo XHR o Fetch, no Document.
- La respuesta no es HTML sino JSON → mejor todavía. Copiar el JSON igualmente.
- Aparecen tokens CSRF que cambian en cada petición → copiarlos también, los gestionaremos.

Cualquier duda, mejor sobre-capturar que infra-capturar. **Mejor que sobre archivo de 10 MB que perder otra vuelta para volver a capturar.**
