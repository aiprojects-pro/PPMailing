"""Tests del sistema de búsquedas programadas."""

import json
from datetime import datetime, timedelta

import pytest


# -----------------------------------------------------------------------------
# Cálculo de próxima ejecución
# -----------------------------------------------------------------------------

def test_compute_next_run_simple():
    from webui.scheduling import compute_next_run

    base = datetime(2026, 5, 19, 10, 0, 0)
    nxt = compute_next_run("simple", interval_minutes=1440, base_time=base)
    expected = base + timedelta(days=1)
    assert nxt == expected


def test_compute_next_run_simple_respects_minimum():
    """Aunque pidas 5 min, el mínimo es 60."""
    from webui.scheduling import compute_next_run

    base = datetime(2026, 5, 19, 10, 0, 0)
    nxt = compute_next_run("simple", interval_minutes=5, base_time=base)
    expected = base + timedelta(minutes=60)
    assert nxt == expected


def test_compute_next_run_cron():
    from webui.scheduling import compute_next_run

    base = datetime(2026, 5, 19, 10, 0, 0)
    # Diariamente a las 6:00
    nxt = compute_next_run("cron", cron_expr="0 6 * * *", base_time=base)
    expected = datetime(2026, 5, 20, 6, 0, 0)
    assert nxt == expected


def test_compute_next_run_cron_minimum():
    """Aun con cron, mínimo 1h entre ejecuciones."""
    from webui.scheduling import compute_next_run

    base = datetime(2026, 5, 19, 10, 0, 0)
    # "Cada minuto" pediría las 10:01, pero el mínimo lo lleva a 11:00
    nxt = compute_next_run("cron", cron_expr="* * * * *", base_time=base)
    assert nxt >= base + timedelta(minutes=60)


def test_compute_next_run_invalid_cron():
    from webui.scheduling import compute_next_run

    with pytest.raises(ValueError):
        compute_next_run("cron", cron_expr="not a valid cron",
                          base_time=datetime.now())


def test_validate_cron_expression():
    from webui.scheduling import validate_cron_expression
    assert validate_cron_expression("0 6 * * 1") is None
    assert validate_cron_expression("*/15 * * * *") is None
    assert validate_cron_expression("not-valid") is not None
    assert validate_cron_expression("") is not None


# -----------------------------------------------------------------------------
# CRUD
# -----------------------------------------------------------------------------

def test_create_schedule_simple(app, admin_client):
    from webui.scheduling import get_schedule

    r = admin_client.post("/schedules/new", data={
        "name": "Test diaria",
        "segmento": "admin_fincas",
        "ambito_kind": "subset",
        "ambito": "andalucia",
        "max_paginas": "1",
        "schedule_kind": "simple",
        "simple_preset": "daily",
    }, follow_redirects=True)
    assert b"creada" in r.data

    # Verificar en BD
    from webui.db import new_connection
    conn = new_connection()
    rows = conn.execute("SELECT * FROM scheduled_searches ORDER BY id DESC").fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0]["name"] == "Test diaria"
    assert rows[0]["schedule_kind"] == "simple"
    assert rows[0]["enabled"] == 1


def test_create_schedule_cron(admin_client):
    r = admin_client.post("/schedules/new", data={
        "name": "Test cron",
        "segmento": "admin_fincas",
        "ambito_kind": "subset",
        "ambito": "andalucia",
        "max_paginas": "1",
        "schedule_kind": "cron",
        "cron_expr": "0 6 * * 1",
    }, follow_redirects=True)
    assert b"creada" in r.data

    from webui.db import new_connection
    conn = new_connection()
    rows = conn.execute("SELECT * FROM scheduled_searches ORDER BY id DESC").fetchall()
    conn.close()
    assert rows[0]["cron_expr"] == "0 6 * * 1"


def test_create_schedule_radius(admin_client):
    points = [{"latitude": 40.4168, "longitude": -3.7038, "radius": 5000}]
    r = admin_client.post("/schedules/new", data={
        "name": "Test radio",
        "segmento": "admin_fincas",
        "ambito_kind": "radius",
        "points_json": json.dumps(points),
        "max_paginas": "1",
        "schedule_kind": "simple",
        "simple_preset": "weekly",
    }, follow_redirects=True)
    assert b"creada" in r.data

    from webui.db import new_connection
    conn = new_connection()
    rows = conn.execute(
        "SELECT * FROM scheduled_searches ORDER BY id DESC LIMIT 1"
    ).fetchall()
    conn.close()
    assert rows[0]["ambito_kind"] == "radius"
    pts = json.loads(rows[0]["points_json"])
    assert len(pts) == 1


def test_create_schedule_invalid_cron(admin_client):
    r = admin_client.post("/schedules/new", data={
        "name": "Test",
        "segmento": "admin_fincas",
        "ambito_kind": "subset",
        "ambito": "andalucia",
        "max_paginas": "1",
        "schedule_kind": "cron",
        "cron_expr": "bogus",
    }, follow_redirects=True)
    assert b"cron inv" in r.data or b"cron Inv" in r.data


def test_create_schedule_invalid_segment(admin_client):
    r = admin_client.post("/schedules/new", data={
        "name": "Test",
        "segmento": "nonexistent_seg",
        "ambito_kind": "subset",
        "ambito": "andalucia",
        "max_paginas": "1",
        "schedule_kind": "simple",
        "simple_preset": "weekly",
    }, follow_redirects=True)
    assert b"Segmento desconocido" in r.data


def test_create_schedule_requires_name(admin_client):
    r = admin_client.post("/schedules/new", data={
        "name": "",
        "segmento": "admin_fincas",
        "ambito_kind": "subset",
        "ambito": "andalucia",
        "max_paginas": "1",
        "schedule_kind": "simple",
        "simple_preset": "weekly",
    }, follow_redirects=True)
    assert b"nombre" in r.data


def test_toggle_schedule(app, admin_client):
    """Activar/desactivar una programación."""
    admin_client.post("/schedules/new", data={
        "name": "X",
        "segmento": "admin_fincas",
        "ambito_kind": "subset",
        "ambito": "andalucia",
        "max_paginas": "1",
        "schedule_kind": "simple",
        "simple_preset": "weekly",
    })
    from webui.db import new_connection
    conn = new_connection()
    sid = conn.execute("SELECT id FROM scheduled_searches").fetchone()["id"]
    conn.close()

    # Desactivar
    r = admin_client.post(f"/schedules/{sid}/toggle", data={"enabled": "0"})
    assert r.status_code == 302
    conn = new_connection()
    state = conn.execute(
        "SELECT enabled FROM scheduled_searches WHERE id=?", (sid,)
    ).fetchone()
    conn.close()
    assert state["enabled"] == 0

    # Reactivar
    admin_client.post(f"/schedules/{sid}/toggle", data={"enabled": "1"})
    conn = new_connection()
    state = conn.execute(
        "SELECT enabled, failure_count FROM scheduled_searches WHERE id=?", (sid,)
    ).fetchone()
    conn.close()
    assert state["enabled"] == 1
    assert state["failure_count"] == 0


def test_delete_schedule(app, admin_client):
    admin_client.post("/schedules/new", data={
        "name": "ToDelete",
        "segmento": "admin_fincas",
        "ambito_kind": "subset",
        "ambito": "andalucia",
        "max_paginas": "1",
        "schedule_kind": "simple",
        "simple_preset": "daily",
    })
    from webui.db import new_connection
    conn = new_connection()
    sid = conn.execute("SELECT id FROM scheduled_searches").fetchone()["id"]
    conn.close()

    r = admin_client.post(f"/schedules/{sid}/delete")
    assert r.status_code == 302

    conn = new_connection()
    row = conn.execute(
        "SELECT 1 FROM scheduled_searches WHERE id=?", (sid,)
    ).fetchone()
    conn.close()
    assert row is None


def test_user_cannot_toggle_others_schedule(app, admin_client, user_client, second_user_client):
    """Alice no puede pausar la programación de Bob."""
    # Bob crea una programación
    second_user_client.post("/schedules/new", data={
        "name": "Bob's schedule",
        "segmento": "admin_fincas",
        "ambito_kind": "subset",
        "ambito": "andalucia",
        "max_paginas": "1",
        "schedule_kind": "simple",
        "simple_preset": "weekly",
    })
    from webui.db import new_connection
    conn = new_connection()
    sid = conn.execute("SELECT id FROM scheduled_searches").fetchone()["id"]
    conn.close()

    # Alice intenta tocarla
    r = user_client.post(f"/schedules/{sid}/toggle", data={"enabled": "0"})
    assert r.status_code == 403

    r = user_client.post(f"/schedules/{sid}/delete")
    assert r.status_code == 403


def test_admin_can_modify_any_schedule(app, admin_client, user_client):
    """Admin sí puede tocar programaciones de otros."""
    user_client.post("/schedules/new", data={
        "name": "Alice schedule",
        "segmento": "admin_fincas",
        "ambito_kind": "subset",
        "ambito": "andalucia",
        "max_paginas": "1",
        "schedule_kind": "simple",
        "simple_preset": "weekly",
    })
    from webui.db import new_connection
    conn = new_connection()
    sid = conn.execute("SELECT id FROM scheduled_searches").fetchone()["id"]
    conn.close()

    r = admin_client.post(f"/schedules/{sid}/toggle", data={"enabled": "0"})
    assert r.status_code == 302  # OK


def test_unauthenticated_cannot_access_schedules(client):
    r = client.get("/schedules/", follow_redirects=False)
    assert r.status_code == 302
    assert "/login" in r.headers["Location"]


# -----------------------------------------------------------------------------
# Worker (no ejecutamos jobs reales, solo validamos lógica de marcado)
# -----------------------------------------------------------------------------

def test_failure_count_disables_after_max(app):
    """Tras N fallos consecutivos, la programación se deshabilita sola."""
    from webui.scheduling import _mark_schedule_failed, MAX_FAILURES
    from webui.db import new_connection

    # Crear una programación a mano
    conn = new_connection()
    admin_id = conn.execute("SELECT id FROM users WHERE username='admin'").fetchone()["id"]
    conn.execute(
        "INSERT INTO scheduled_searches "
        "(user_id, name, segmento, ambito_kind, ambito, max_paginas, "
        " schedule_kind, interval_minutes, enabled, next_run_at, created_at) "
        "VALUES (?, 'X', 'admin_fincas', 'subset', 'andalucia', 1, "
        " 'simple', 1440, 1, ?, ?)",
        (admin_id, datetime.now().isoformat(), datetime.now().isoformat()),
    )
    sid = conn.execute(
        "SELECT id FROM scheduled_searches ORDER BY id DESC LIMIT 1"
    ).fetchone()["id"]
    conn.close()

    # Marcar N-1 fallos: aún debe estar activa
    for i in range(MAX_FAILURES - 1):
        _mark_schedule_failed(sid, f"fail {i}")

    conn = new_connection()
    row = conn.execute(
        "SELECT enabled, failure_count FROM scheduled_searches WHERE id=?", (sid,)
    ).fetchone()
    conn.close()
    assert row["enabled"] == 1
    assert row["failure_count"] == MAX_FAILURES - 1

    # Un fallo más → desactivada
    _mark_schedule_failed(sid, "fail final")
    conn = new_connection()
    row = conn.execute(
        "SELECT enabled, failure_count, last_error FROM scheduled_searches WHERE id=?",
        (sid,),
    ).fetchone()
    conn.close()
    assert row["enabled"] == 0
    assert row["failure_count"] == MAX_FAILURES
    assert "Auto-desactivada" in row["last_error"]
