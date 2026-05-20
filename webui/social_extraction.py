"""
Extracción de URLs de redes sociales y WhatsApp desde HTML de páginas web.

Cubre las 6 redes que dejaste seleccionadas: LinkedIn, Instagram, Facebook,
Twitter/X, YouTube, TikTok.

Estrategia:
  - Regex robustas que capturan handles y URLs canónicas.
  - Filtros para descartar URLs genéricas de share/intent/sharer (típicas
    de botones "Compartir en X") que NO representan al negocio.
  - Si la web tiene varias URLs de la misma red, nos quedamos con la
    "más probable" (la más corta, suele ser la página principal vs
    sub-paths como /posts/...).

NO descargamos la web aquí: este módulo recibe el HTML como input. La
descarga la hace el caller (job runner o action manual desde el detalle
del lead). De esta forma el módulo es testable sin red.
"""

import re
from urllib.parse import urlparse


# Patrones por red. Cada uno captura desde "https://..." hasta el handle.
# Usamos non-capturing groups (?:...) para optimizar y .*? para no comerse
# múltiples URLs en una misma línea.
PATTERNS = {
    "linkedin": [
        # /company/<slug>, /in/<slug>, /school/<slug>
        re.compile(
            r"https?://(?:[\w-]+\.)?linkedin\.com/(?:company|in|school|pub)/[\w%\-.~]+/?",
            re.IGNORECASE,
        ),
    ],
    "instagram": [
        re.compile(
            r"https?://(?:www\.)?instagram\.com/(?!p/|explore/|reel/|tv/)([\w._]+)/?",
            re.IGNORECASE,
        ),
    ],
    "facebook": [
        # /pages/<slug>/<id>, /<username>, /people/<slug>/<id>
        re.compile(
            r"https?://(?:www\.|m\.|es-es\.)?facebook\.com/"
            r"(?!sharer|sharer\.php|share|dialog|tr\?|plugins)[\w.\-]+/?",
            re.IGNORECASE,
        ),
    ],
    "twitter": [
        re.compile(
            r"https?://(?:www\.)?(?:twitter|x)\.com/(?!share|intent|home|search)[\w]+/?",
            re.IGNORECASE,
        ),
    ],
    "youtube": [
        # @handle, /c/<slug>, /channel/<id>, /user/<slug>
        re.compile(
            r"https?://(?:www\.)?youtube\.com/(?:@[\w\-]+|c/[\w\-]+|channel/[\w\-]+|user/[\w\-]+)/?",
            re.IGNORECASE,
        ),
    ],
    "tiktok": [
        re.compile(
            r"https?://(?:www\.)?tiktok\.com/@[\w._]+/?",
            re.IGNORECASE,
        ),
    ],
}


# URLs que NUNCA son del negocio (botones genéricos de compartir, oficiales).
# Filtramos por substring después del match.
_BLOCKLIST_SUBSTRINGS = {
    "linkedin": [
        "/sharing", "/sharearticle", "linkedin.com/in/", # NO: /in/ sí es válido
    ],
    # Quitamos /in/ del blocklist porque es legítimo para perfiles personales
    "instagram": ["/p/", "/reel/", "/explore/", "/tv/"],
    "facebook": ["/sharer", "/share", "/dialog", "/plugins", "/tr?"],
    "twitter": ["/share", "/intent", "/home", "/search"],
    "youtube": ["/watch", "/results", "/feed"],
    "tiktok": [],
}

# Re-definimos sin la entrada errónea
_BLOCKLIST_SUBSTRINGS["linkedin"] = ["/sharing", "/sharearticle"]


def _is_blocked(url: str, kind: str) -> bool:
    url_low = url.lower()
    return any(sub in url_low for sub in _BLOCKLIST_SUBSTRINGS.get(kind, []))


def _normalize(url: str) -> str:
    """Quita query strings, fragmentos y trailing slash. Devuelve URL canónica."""
    if not url:
        return ""
    p = urlparse(url)
    if not p.scheme or not p.netloc:
        return url.strip().rstrip("/")
    path = (p.path or "").rstrip("/")
    return f"{p.scheme}://{p.netloc.lower()}{path}"


def _pick_best(candidates: list[str]) -> str:
    """
    De varias URLs candidatas para la misma red, elige la más probable de
    representar al negocio: la más corta (página principal) sin paths extra.
    """
    if not candidates:
        return ""
    # Deduplicar normalizadas
    seen = []
    norm_set = set()
    for c in candidates:
        n = _normalize(c)
        if n and n not in norm_set:
            seen.append(n)
            norm_set.add(n)
    if not seen:
        return ""
    return min(seen, key=len)


def extract_social_urls(html: str) -> dict:
    """
    Extrae URLs de las 6 redes sociales a partir del HTML.

    Devuelve un dict:
        {
          "linkedin_url": "https://linkedin.com/company/...",
          "instagram_url": "https://instagram.com/...",
          "facebook_url": ...
          "twitter_url": ...
          "youtube_url": ...
          "tiktok_url": ...
        }

    Si una red no aparece o solo aparecen URLs filtradas, su campo es "".
    """
    out = {f"{k}_url": "" for k in PATTERNS}
    if not html:
        return out

    for kind, patterns in PATTERNS.items():
        candidates = []
        for pat in patterns:
            for match in pat.finditer(html):
                url = match.group(0)
                if not _is_blocked(url, kind):
                    candidates.append(url)
        out[f"{kind}_url"] = _pick_best(candidates)

    return out


def extract_social_from_url(url: str, timeout: float = 8.0) -> dict:
    """
    Versión que sí hace la petición HTTP (utilizada en la acción manual
    "Re-extraer redes" desde el detalle del lead).

    Si no se puede descargar la web, devuelve dict con campos vacíos.
    """
    import requests
    blank = {f"{k}_url": "" for k in PATTERNS}
    if not url or not url.startswith(("http://", "https://")):
        return blank
    try:
        r = requests.get(
            url,
            headers={"User-Agent": "PPMailing-Bot/1.0 (+leads)"},
            timeout=timeout,
            allow_redirects=True,
        )
        if r.status_code != 200 or not r.text:
            return blank
        # Limitar tamaño para no procesar 10MB de HTML
        html = r.text[:500_000]
        return extract_social_urls(html)
    except Exception:
        return blank
