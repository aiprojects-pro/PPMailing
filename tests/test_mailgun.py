"""Tests de integración con Mailgun: config, push, webhook firma."""

import hashlib
import hmac
import json
import time

import pytest


# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------

def test_mailgun_not_configured_by_default(app):
    from webui.mailgun import is_configured
    with app.app_context():
        assert is_configured() is False


def test_set_and_get_mailgun_config(app):
    from webui.mailgun import set_mailgun_config, is_configured, _get_config
    with app.app_context():
        set_mailgun_config(
            api_key="key-test123",
            domain="mg.example.com",
            base_url="https://api.eu.mailgun.net",
            webhook_signing_key="whsk-abc",
        )
        assert is_configured()
        cfg = _get_config()
        assert cfg["api_key"] == "key-test123"
        assert cfg["domain"] == "mg.example.com"
        assert cfg["base_url"] == "https://api.eu.mailgun.net"
        assert cfg["webhook_signing_key"] == "whsk-abc"


def test_clear_mailgun_config(app):
    from webui.mailgun import set_mailgun_config, clear_mailgun_config, is_configured
    with app.app_context():
        set_mailgun_config("k", "d", "https://x.com", "w")
        assert is_configured()
        clear_mailgun_config()
        assert not is_configured()


def test_mask_returns_masked(app):
    from webui.mailgun import set_mailgun_config, mask_mailgun_config
    with app.app_context():
        set_mailgun_config(
            api_key="key-AIzaSyD1234567890abcdef",
            domain="mg.example.com",
            base_url="https://api.mailgun.net",
            webhook_signing_key="whsk-1234567890",
        )
        masked = mask_mailgun_config()
        assert masked["configured"] is True
        assert "AIza" not in masked["api_key_masked"] or "•" in masked["api_key_masked"]
        assert masked["domain"] == "mg.example.com"


def test_admin_mailgun_settings_endpoint(admin_client):
    r = admin_client.post("/admin/mailgun-settings", data={
        "api_key": "key-test",
        "domain": "mg.example.com",
        "base_url": "https://api.mailgun.net",
        "webhook_signing_key": "whsk-test",
    }, follow_redirects=True)
    assert b"guardada" in r.data


def test_admin_mailgun_clear(admin_client):
    admin_client.post("/admin/mailgun-settings", data={
        "api_key": "k", "domain": "d", "base_url": "https://x.com",
        "webhook_signing_key": "w",
    })
    r = admin_client.post("/admin/mailgun-settings", data={
        "action": "clear",
    }, follow_redirects=True)
    assert b"eliminada" in r.data


def test_user_cannot_configure_mailgun(user_client):
    r = user_client.post("/admin/mailgun-settings", data={
        "api_key": "k", "domain": "d", "base_url": "https://x.com",
        "webhook_signing_key": "w",
    })
    assert r.status_code == 403


def test_base_url_validation(admin_client):
    """Base URL sin http:// debe rechazarse."""
    r = admin_client.post("/admin/mailgun-settings", data={
        "api_key": "k", "domain": "d",
        "base_url": "not-a-url",
        "webhook_signing_key": "w",
    }, follow_redirects=True)
    assert b"http" in r.data.lower()


# -----------------------------------------------------------------------------
# Webhook signature
# -----------------------------------------------------------------------------

def _sign(timestamp: str, token: str, signing_key: str) -> str:
    return hmac.new(
        key=signing_key.encode("utf-8"),
        msg=(timestamp + token).encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()


def test_webhook_signature_valid(app):
    from webui.mailgun import set_mailgun_config, verify_webhook_signature
    with app.app_context():
        set_mailgun_config("k", "d", "https://x.com", "test-signing-key")
        ts = "1700000000"
        tok = "abc123"
        sig = _sign(ts, tok, "test-signing-key")
        assert verify_webhook_signature(ts, tok, sig) is True


def test_webhook_signature_invalid_key(app):
    from webui.mailgun import set_mailgun_config, verify_webhook_signature
    with app.app_context():
        set_mailgun_config("k", "d", "https://x.com", "real-key")
        sig = _sign("1700000000", "abc", "WRONG-KEY")
        assert verify_webhook_signature("1700000000", "abc", sig) is False


def test_webhook_signature_no_config(app):
    from webui.mailgun import verify_webhook_signature
    with app.app_context():
        # Sin webhook_signing_key configurada, todo se rechaza
        assert verify_webhook_signature("1", "2", "3") is False


def test_webhook_endpoint_rejects_bad_signature(app, client):
    """POST /webhooks/mailgun con firma inválida → 401."""
    from webui.mailgun import set_mailgun_config
    with app.app_context():
        set_mailgun_config("k", "d", "https://x.com", "signing-key")

    r = client.post("/webhooks/mailgun", data={
        "timestamp": "1700000000",
        "token": "abc",
        "signature": "wrong-signature",
        "event": "delivered",
        "recipient": "x@y.com",
    })
    assert r.status_code == 401


def test_webhook_endpoint_accepts_valid(app, client):
    """POST /webhooks/mailgun con firma válida procesa el evento."""
    from webui.mailgun import set_mailgun_config
    from webui.db import new_connection
    from datetime import datetime

    with app.app_context():
        set_mailgun_config("k", "d", "https://x.com", "secret-key")

        # Crear un lead con email conocido
        conn = new_connection()
        conn.execute(
            "INSERT INTO jobs (id, user_id, segmento, ambito, status, queued_at) "
            "VALUES ('wjob1', 1, 'admin_fincas', 'andalucia', 'done', ?)",
            (datetime.now().isoformat(timespec="seconds"),),
        )
        conn.execute(
            "INSERT INTO leads_master (place_id, segmento, nombre, email, "
            "first_seen_at, last_seen_at, last_seen_job_id) "
            "VALUES ('pwh1', 'admin_fincas', 'X', 'webhook@test.com', ?, ?, 'wjob1')",
            (datetime.now().isoformat(timespec="seconds"),
             datetime.now().isoformat(timespec="seconds")),
        )
        conn.close()

    ts = str(int(time.time()))
    tok = "tok-test"
    sig = _sign(ts, tok, "secret-key")

    r = client.post("/webhooks/mailgun", data={
        "timestamp": ts,
        "token": tok,
        "signature": sig,
        "event": "delivered",
        "recipient": "webhook@test.com",
    })
    assert r.status_code == 200
    data = r.get_json()
    assert data["status"] == "ok"
    assert data["updated"] == 1

    # Verificar que el lead pasó a 'contactado'
    conn = new_connection()
    row = conn.execute(
        "SELECT estado, fecha_ultimo_contacto FROM leads_master WHERE place_id='pwh1'"
    ).fetchone()
    conn.close()
    assert row["estado"] == "contactado"
    assert row["fecha_ultimo_contacto"] != ""


def test_webhook_complained_marks_descartado(app, client):
    """Un complained marca el lead como descartado."""
    from webui.mailgun import set_mailgun_config
    from webui.db import new_connection
    from datetime import datetime

    with app.app_context():
        set_mailgun_config("k", "d", "https://x.com", "secret-key")
        conn = new_connection()
        conn.execute(
            "INSERT INTO jobs (id, user_id, segmento, ambito, status, queued_at) "
            "VALUES ('wjob2', 1, 'admin_fincas', 'andalucia', 'done', ?)",
            (datetime.now().isoformat(timespec="seconds"),),
        )
        conn.execute(
            "INSERT INTO leads_master (place_id, segmento, nombre, email, estado, "
            "first_seen_at, last_seen_at, last_seen_job_id) "
            "VALUES ('pwh2', 'admin_fincas', 'X', 'angry@test.com', 'contactado', "
            " ?, ?, 'wjob2')",
            (datetime.now().isoformat(timespec="seconds"),
             datetime.now().isoformat(timespec="seconds")),
        )
        conn.close()

    ts = str(int(time.time()))
    tok = "x"
    sig = _sign(ts, tok, "secret-key")
    r = client.post("/webhooks/mailgun", data={
        "timestamp": ts, "token": tok, "signature": sig,
        "event": "complained", "recipient": "angry@test.com",
    })
    assert r.status_code == 200

    conn = new_connection()
    row = conn.execute(
        "SELECT estado FROM leads_master WHERE place_id='pwh2'"
    ).fetchone()
    conn.close()
    assert row["estado"] == "descartado"


def test_webhook_json_format(app, client):
    """Mailgun también puede mandar JSON estructurado, no solo form-encoded."""
    from webui.mailgun import set_mailgun_config
    from webui.db import new_connection
    from datetime import datetime

    with app.app_context():
        set_mailgun_config("k", "d", "https://x.com", "secret-key")
        conn = new_connection()
        conn.execute(
            "INSERT INTO jobs (id, user_id, segmento, ambito, status, queued_at) "
            "VALUES ('wjob3', 1, 'admin_fincas', 'andalucia', 'done', ?)",
            (datetime.now().isoformat(timespec="seconds"),),
        )
        conn.execute(
            "INSERT INTO leads_master (place_id, segmento, nombre, email, "
            "first_seen_at, last_seen_at, last_seen_job_id) "
            "VALUES ('pwh3', 'admin_fincas', 'X', 'json@test.com', ?, ?, 'wjob3')",
            (datetime.now().isoformat(timespec="seconds"),
             datetime.now().isoformat(timespec="seconds")),
        )
        conn.close()

    ts = str(int(time.time()))
    tok = "json-tok"
    sig = _sign(ts, tok, "secret-key")

    payload = {
        "signature": {"timestamp": ts, "token": tok, "signature": sig},
        "event-data": {"event": "delivered", "recipient": "json@test.com"},
    }
    r = client.post(
        "/webhooks/mailgun",
        data=json.dumps(payload),
        content_type="application/json",
    )
    assert r.status_code == 200


def test_webhook_unknown_recipient_no_error(app, client):
    """Si Mailgun manda un evento de un email que no tenemos, no crashea."""
    from webui.mailgun import set_mailgun_config
    with app.app_context():
        set_mailgun_config("k", "d", "https://x.com", "secret-key")

    ts = str(int(time.time()))
    tok = "x"
    sig = _sign(ts, tok, "secret-key")
    r = client.post("/webhooks/mailgun", data={
        "timestamp": ts, "token": tok, "signature": sig,
        "event": "delivered", "recipient": "nobody@nowhere.com",
    })
    assert r.status_code == 200
    assert r.get_json()["updated"] == 0


def test_mailgun_push_requires_config(admin_client):
    r = admin_client.get("/mailgun/push", follow_redirects=True)
    assert b"no est" in r.data  # "no está configurado"


def test_mailgun_push_requires_login(client):
    r = client.get("/mailgun/push", follow_redirects=False)
    assert r.status_code == 302
    assert "/login" in r.headers["Location"]
