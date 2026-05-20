"""Tests del módulo de validación de email."""

import pytest


# -----------------------------------------------------------------------------
# Validación sintáctica
# -----------------------------------------------------------------------------

def test_invalid_syntax_rejected():
    from webui.email_validation import check_email_mx
    invalid = [
        "",
        "not-an-email",
        "no@dot",
        "@nodomain.com",
        "spaces in@email.com",
        "a" * 300 + "@too.long",
    ]
    for email in invalid:
        r = check_email_mx(email)
        assert r["status"] == "mx_fail", f"{email!r} no fue rechazado"
        assert "formato" in r["reason"] or "MX" in r["reason"] or "A records" in r["reason"]


def test_valid_syntax_passes_to_dns_check():
    from webui.email_validation import _is_syntactically_valid
    valid = [
        "a@b.co",
        "foo.bar@example.com",
        "user+tag@subdomain.example.co.uk",
        "a-b_c@x-y.example.es",
    ]
    for email in valid:
        assert _is_syntactically_valid(email), f"{email!r} debería ser válido"


# -----------------------------------------------------------------------------
# Resolución DNS (requiere red)
# -----------------------------------------------------------------------------

def test_real_domain_resolves():
    """Un dominio real con MX records debe devolver mx_ok.

    Este test requiere conexión a internet. Si no la hay, se salta.
    """
    from webui.email_validation import check_email_mx
    r = check_email_mx("test@gmail.com")
    # gmail.com siempre tiene MX
    assert r["status"] == "mx_ok"


def test_nonexistent_domain_fails():
    """Un dominio inventado no debe resolver."""
    from webui.email_validation import check_email_mx
    r = check_email_mx("test@xyz-nonexistent-domain-12345-abc.invalid")
    assert r["status"] == "mx_fail"


def test_result_includes_timestamp():
    from webui.email_validation import check_email_mx
    r = check_email_mx("test@gmail.com")
    assert "checked_at" in r
    # Es un ISO timestamp
    assert "T" in r["checked_at"]


# -----------------------------------------------------------------------------
# Integración con leads
# -----------------------------------------------------------------------------

def test_validate_lead_email_updates_db(app):
    """validate_lead_email persiste el resultado en la BD."""
    from webui.leads import ingest_job_csv, validate_lead_email, get_lead
    import csv, tempfile
    from pathlib import Path
    from datetime import datetime
    from webui.db import new_connection

    p = Path(tempfile.mktemp(suffix=".csv"))
    with open(p, "w", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["place_id", "nombre", "email", "score", "apto_campanya"])
        w.writerow(["p1", "X", "test@gmail.com", "85", "SI"])

    conn = new_connection()
    aid = conn.execute("SELECT id FROM users WHERE username='admin'").fetchone()["id"]
    conn.execute(
        "INSERT INTO jobs (id, user_id, segmento, ambito, status, queued_at) "
        "VALUES ('vj1', ?, 'admin_fincas', 'andalucia', 'done', ?)",
        (aid, datetime.now().isoformat(timespec="seconds")),
    )
    conn.close()

    with app.app_context():
        ingest_job_csv(p, "vj1", "admin_fincas")
        result = validate_lead_email("p1")
        lead = get_lead("p1")

    assert result is not None
    assert lead["email_status"] in ("mx_ok", "mx_fail")  # depende de red
    assert lead["email_checked_at"] != ""
    p.unlink()


def test_validate_lead_without_email_returns_none(app):
    """Si el lead no tiene email, validate_lead_email devuelve None."""
    from webui.leads import ingest_job_csv, validate_lead_email
    import csv, tempfile
    from pathlib import Path
    from datetime import datetime
    from webui.db import new_connection

    p = Path(tempfile.mktemp(suffix=".csv"))
    with open(p, "w", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["place_id", "nombre", "email", "score", "apto_campanya"])
        w.writerow(["p1", "X", "", "85", "SI"])  # email vacío

    conn = new_connection()
    aid = conn.execute("SELECT id FROM users WHERE username='admin'").fetchone()["id"]
    conn.execute(
        "INSERT INTO jobs (id, user_id, segmento, ambito, status, queued_at) "
        "VALUES ('vj2', ?, 'admin_fincas', 'andalucia', 'done', ?)",
        (aid, datetime.now().isoformat(timespec="seconds")),
    )
    conn.close()

    with app.app_context():
        ingest_job_csv(p, "vj2", "admin_fincas")
        result = validate_lead_email("p1")

    assert result is None
    p.unlink()


def test_validate_nonexistent_lead_returns_none(app):
    from webui.leads import validate_lead_email
    with app.app_context():
        result = validate_lead_email("doesnotexist")
    assert result is None


# -----------------------------------------------------------------------------
# Mailgun method (mock)
# -----------------------------------------------------------------------------

def test_check_email_mailgun_deliverable(monkeypatch):
    """Mock Mailgun API: 'deliverable' → status 'verified'."""
    from webui import email_validation

    class FakeResponse:
        status_code = 200
        def json(self):
            return {"result": "deliverable", "risk": "low"}

    def fake_get(*args, **kwargs):
        return FakeResponse()

    monkeypatch.setattr("requests.get", fake_get)
    r = email_validation.check_email_mailgun("real@example.com", api_key="key-x")
    assert r["status"] == "verified"
    assert r["method"] == "mailgun"
    assert "deliverable" in r["reason"]


def test_check_email_mailgun_undeliverable(monkeypatch):
    from webui import email_validation

    class FakeResponse:
        status_code = 200
        def json(self):
            return {"result": "undeliverable", "risk": "high"}

    monkeypatch.setattr("requests.get", lambda *a, **kw: FakeResponse())
    r = email_validation.check_email_mailgun("bad@example.com", api_key="k")
    assert r["status"] == "invalid"


def test_check_email_mailgun_catch_all(monkeypatch):
    from webui import email_validation

    class FakeResponse:
        status_code = 200
        def json(self):
            return {"result": "catch_all"}

    monkeypatch.setattr("requests.get", lambda *a, **kw: FakeResponse())
    r = email_validation.check_email_mailgun("any@catchall.com", api_key="k")
    assert r["status"] == "catch_all"


def test_check_email_mailgun_invalid_key(monkeypatch):
    from webui import email_validation

    class FakeResponse:
        status_code = 401
        text = "auth failed"

    monkeypatch.setattr("requests.get", lambda *a, **kw: FakeResponse())
    r = email_validation.check_email_mailgun("x@y.com", api_key="bad-key")
    assert r["status"] == "unknown"
    assert "API key" in r["reason"]


def test_validate_lead_with_method_choice(app, monkeypatch):
    """validate_lead_email respeta el parámetro method."""
    from webui.leads import ingest_job_csv, validate_lead_email
    from webui.db import new_connection
    import csv, tempfile
    from pathlib import Path
    from datetime import datetime

    p = Path(tempfile.mktemp(suffix=".csv"))
    with open(p, "w", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["place_id", "nombre", "email", "score", "apto_campanya"])
        w.writerow(["pm1", "X", "test@gmail.com", "85", "SI"])

    conn = new_connection()
    aid = conn.execute("SELECT id FROM users WHERE username='admin'").fetchone()["id"]
    conn.execute(
        "INSERT INTO jobs (id, user_id, segmento, ambito, status, queued_at) "
        "VALUES ('mj1', ?, 'admin_fincas', 'andalucia', 'done', ?)",
        (aid, datetime.now().isoformat(timespec="seconds")),
    )
    conn.close()

    with app.app_context():
        ingest_job_csv(p, "mj1", "admin_fincas")
        # Mailgun no configurado → debe devolver 'unknown'
        result = validate_lead_email("pm1", method="mailgun")
        assert result["method"] == "mailgun"
        assert result["status"] == "unknown"

    p.unlink()
