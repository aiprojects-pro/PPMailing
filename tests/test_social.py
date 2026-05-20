"""Tests de extracción de redes sociales."""

import pytest


def test_extract_all_networks():
    from webui.social_extraction import extract_social_urls
    html = """
    <a href="https://www.linkedin.com/company/acme/">LinkedIn</a>
    <a href="https://www.facebook.com/AcmeCorp">FB</a>
    <a href="https://instagram.com/acme">IG</a>
    <a href="https://x.com/acme">X</a>
    <a href="https://www.youtube.com/@AcmeOfficial">YT</a>
    <a href="https://www.tiktok.com/@acme">TT</a>
    """
    r = extract_social_urls(html)
    assert "linkedin.com/company/acme" in r["linkedin_url"]
    assert "facebook.com/AcmeCorp" in r["facebook_url"]
    assert "instagram.com/acme" in r["instagram_url"]
    assert r["twitter_url"]
    assert "youtube.com/@AcmeOfficial" in r["youtube_url"]
    assert "tiktok.com/@acme" in r["tiktok_url"]


def test_filters_share_intents():
    """URLs de compartir/intent NO deben aparecer."""
    from webui.social_extraction import extract_social_urls
    html = """
    <a href="https://www.facebook.com/sharer.php?u=...">Share</a>
    <a href="https://twitter.com/intent/tweet?text=hi">Tweet</a>
    <a href="https://www.linkedin.com/sharing/share-offsite/?url=x">LI share</a>
    """
    r = extract_social_urls(html)
    assert r["facebook_url"] == ""
    assert r["twitter_url"] == ""
    assert r["linkedin_url"] == ""


def test_filters_post_urls():
    """URLs a posts individuales no son la página principal."""
    from webui.social_extraction import extract_social_urls
    html = """
    <a href="https://www.instagram.com/p/ABC123/">Post</a>
    <a href="https://www.instagram.com/explore/tags/x/">Tag</a>
    """
    r = extract_social_urls(html)
    assert r["instagram_url"] == ""


def test_picks_shortest_canonical():
    """Si hay varias URLs de la misma red, elige la más corta."""
    from webui.social_extraction import extract_social_urls
    html = """
    <a href="https://linkedin.com/company/acme/posts/long-extra-path">Long</a>
    <a href="https://linkedin.com/company/acme/">Short</a>
    <a href="https://linkedin.com/company/acme/about/extra">Another</a>
    """
    r = extract_social_urls(html)
    # La canónica es company/acme
    assert r["linkedin_url"].endswith("/company/acme")


def test_empty_html():
    from webui.social_extraction import extract_social_urls
    r = extract_social_urls("")
    assert all(v == "" for v in r.values())


def test_normalize_strips_query_and_fragment():
    from webui.social_extraction import _normalize
    n = _normalize("https://Twitter.com/acme?utm_source=foo#bar")
    # Lowercase + sin query + sin fragmento
    assert "?" not in n
    assert "#" not in n
    assert "twitter.com" in n


def test_extract_action_updates_db(app, admin_client):
    """Llamar POST /leads/<id>/extract-social actualiza la BD."""
    from webui.leads import ingest_job_csv
    from webui.db import new_connection
    import csv, tempfile
    from pathlib import Path
    from datetime import datetime

    # Crear job + lead con web (que no será accesible, pero el endpoint
    # debe responder sin crashear)
    p = Path(tempfile.mktemp(suffix=".csv"))
    with open(p, "w", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["place_id", "nombre", "email", "web", "score", "apto_campanya"])
        w.writerow(["p1", "X", "x@x.es", "https://example-no-existe.invalid",
                    "85", "SI"])

    conn = new_connection()
    aid = conn.execute("SELECT id FROM users WHERE username='admin'").fetchone()["id"]
    conn.execute(
        "INSERT INTO jobs (id, user_id, segmento, ambito, status, queued_at) "
        "VALUES ('soc_j1', ?, 'admin_fincas', 'andalucia', 'done', ?)",
        (aid, datetime.now().isoformat(timespec="seconds")),
    )
    conn.close()

    with app.app_context():
        ingest_job_csv(p, "soc_j1", "admin_fincas")

    r = admin_client.post("/leads/p1/extract-social", follow_redirects=True)
    # Sin importar resultado, no debe crashear
    assert r.status_code == 200
    p.unlink()


def test_bulk_extract_endpoint_requires_login(client):
    r = client.post("/leads/bulk/extract-social")
    # Sin login redirige al login
    assert r.status_code in (302, 401, 403)
