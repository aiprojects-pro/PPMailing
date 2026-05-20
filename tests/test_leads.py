"""
Tests del sistema de leads: ingesta, dedupe, estados, exportación.
"""

import csv
import tempfile
from datetime import datetime
from pathlib import Path

import pytest


def _create_test_csv(rows: list[dict]) -> Path:
    """Crea un CSV temporal con las filas dadas."""
    p = Path(tempfile.mktemp(suffix=".csv"))
    fieldnames = ["place_id", "nombre", "email", "telefono", "web",
                  "direccion", "localidad", "provincia", "ccaa",
                  "rating", "num_resenas", "score", "apto_campanya"]
    with open(p, "w", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return p


def _create_job_for_test(app, job_id: str = "testjob_lead", segmento: str = "admin_fincas"):
    """Crea un job vacío en BD (necesario por la FK de lead_jobs)."""
    from webui.db import new_connection
    conn = new_connection()
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin'"
    ).fetchone()["id"]
    conn.execute(
        "INSERT OR REPLACE INTO jobs (id, user_id, segmento, ambito, status, queued_at) "
        "VALUES (?, ?, ?, 'andalucia', 'done', ?)",
        (job_id, admin_id, segmento,
         datetime.now().isoformat(timespec="seconds")),
    )
    conn.close()


# -----------------------------------------------------------------------------
# Ingesta y dedupe
# -----------------------------------------------------------------------------

def test_ingest_new_leads(app):
    """Primer volcado: todos los leads se insertan como nuevos."""
    from webui.leads import ingest_job_csv

    csv_path = _create_test_csv([
        {"place_id": "p1", "nombre": "Bar Pepe", "email": "info@barpepe.es",
         "score": "85", "apto_campanya": "SI"},
        {"place_id": "p2", "nombre": "Café Luna", "email": "",
         "score": "60", "apto_campanya": "NO"},
    ])
    _create_job_for_test(app)
    with app.app_context():
        stats = ingest_job_csv(csv_path, "testjob_lead", "admin_fincas")
    assert stats == {"nuevos": 2, "actualizados": 0, "total": 2}
    csv_path.unlink()


def test_ingest_dedupes_by_place_id(app):
    """Segundo volcado del mismo CSV: todo se actualiza, nada se duplica."""
    from webui.leads import ingest_job_csv, list_leads

    csv_path = _create_test_csv([
        {"place_id": "p1", "nombre": "Bar Pepe", "email": "info@barpepe.es",
         "score": "85", "apto_campanya": "SI"},
    ])
    _create_job_for_test(app, "j1")
    with app.app_context():
        ingest_job_csv(csv_path, "j1", "admin_fincas")

    _create_job_for_test(app, "j2")
    with app.app_context():
        stats = ingest_job_csv(csv_path, "j2", "admin_fincas")

    assert stats["nuevos"] == 0
    assert stats["actualizados"] == 1

    with app.app_context():
        rows, total = list_leads()
    assert total == 1
    assert rows[0]["times_seen"] == 2
    csv_path.unlink()


def test_ingest_preserves_state_on_reingest(app):
    """REGRESIÓN CRÍTICA: re-ingestar no debe resetear el estado del lead."""
    from webui.leads import ingest_job_csv, update_lead_state, get_lead

    csv_path = _create_test_csv([
        {"place_id": "p1", "nombre": "Bar", "email": "info@bar.es",
         "score": "85", "apto_campanya": "SI"},
    ])
    _create_job_for_test(app, "j1")
    with app.app_context():
        ingest_job_csv(csv_path, "j1", "admin_fincas")
        # Marcar como contactado
        update_lead_state("p1", "contactado", "Llamé al gerente")

    # Re-ingestar
    _create_job_for_test(app, "j2")
    with app.app_context():
        ingest_job_csv(csv_path, "j2", "admin_fincas")
        lead = get_lead("p1")

    assert lead["estado"] == "contactado"
    assert lead["notas"] == "Llamé al gerente"
    csv_path.unlink()


def test_ingest_resets_email_status_when_email_changes(app):
    """Si el email cambia entre búsquedas, su validación previa se invalida."""
    from webui.leads import ingest_job_csv, validate_lead_email, get_lead

    csv_path1 = _create_test_csv([
        {"place_id": "p1", "nombre": "Bar", "email": "old@bar.es",
         "score": "85", "apto_campanya": "SI"},
    ])
    _create_job_for_test(app, "j1")
    with app.app_context():
        ingest_job_csv(csv_path1, "j1", "admin_fincas")
        # Validar el email (no importa el resultado real, lo forzamos)
        from webui.db import get_db
        get_db().execute(
            "UPDATE leads_master SET email_status='mx_ok' WHERE place_id='p1'"
        )
        lead = get_lead("p1")
        assert lead["email_status"] == "mx_ok"

    # Re-ingestar con email distinto
    csv_path2 = _create_test_csv([
        {"place_id": "p1", "nombre": "Bar", "email": "new@bar.es",
         "score": "85", "apto_campanya": "SI"},
    ])
    _create_job_for_test(app, "j2")
    with app.app_context():
        ingest_job_csv(csv_path2, "j2", "admin_fincas")
        lead = get_lead("p1")

    # El email_status debe haberse reseteado porque el email cambió
    assert lead["email"] == "new@bar.es"
    assert lead["email_status"] == ""
    csv_path1.unlink()
    csv_path2.unlink()


def test_ingest_creates_lead_job_relations(app):
    """Cada ingesta crea una fila en lead_jobs."""
    from webui.leads import ingest_job_csv
    from webui.db import new_connection

    csv_path = _create_test_csv([
        {"place_id": "p1", "nombre": "Bar", "score": "85", "apto_campanya": "SI"},
    ])
    _create_job_for_test(app, "j1")
    _create_job_for_test(app, "j2")
    with app.app_context():
        ingest_job_csv(csv_path, "j1", "admin_fincas")
        ingest_job_csv(csv_path, "j2", "admin_fincas")

    conn = new_connection()
    rows = conn.execute(
        "SELECT job_id FROM lead_jobs WHERE place_id='p1' ORDER BY job_id"
    ).fetchall()
    conn.close()

    assert {r["job_id"] for r in rows} == {"j1", "j2"}
    csv_path.unlink()


def test_ingest_skips_rows_without_place_id(app):
    """Filas sin place_id se descartan silenciosamente (no se pueden deduplicar)."""
    from webui.leads import ingest_job_csv

    csv_path = _create_test_csv([
        {"place_id": "", "nombre": "Sin id", "score": "85", "apto_campanya": "SI"},
        {"place_id": "p1", "nombre": "Con id", "score": "85", "apto_campanya": "SI"},
    ])
    _create_job_for_test(app, "j1")
    with app.app_context():
        stats = ingest_job_csv(csv_path, "j1", "admin_fincas")

    assert stats["nuevos"] == 1
    assert stats["total"] == 1
    csv_path.unlink()


# -----------------------------------------------------------------------------
# Estados de leads
# -----------------------------------------------------------------------------

def test_state_transitions(app):
    """Probar todas las transiciones de estado válidas."""
    from webui.leads import ingest_job_csv, update_lead_state, get_lead

    csv_path = _create_test_csv([
        {"place_id": "p1", "nombre": "X", "score": "85", "apto_campanya": "SI"},
    ])
    _create_job_for_test(app)
    with app.app_context():
        ingest_job_csv(csv_path, "testjob_lead", "admin_fincas")

        for estado in ("contactado", "respondio", "descartado", "nuevo"):
            assert update_lead_state("p1", estado) is True
            assert get_lead("p1")["estado"] == estado

    csv_path.unlink()


def test_invalid_state_rejected(app):
    """Estados no permitidos se rechazan."""
    from webui.leads import update_lead_state
    with app.app_context():
        assert update_lead_state("p1", "invalid_state") is False
        assert update_lead_state("p1", "") is False


def test_contactado_sets_fecha(app):
    """Al marcar como contactado se registra fecha_ultimo_contacto."""
    from webui.leads import ingest_job_csv, update_lead_state, get_lead

    csv_path = _create_test_csv([
        {"place_id": "p1", "nombre": "X", "score": "85", "apto_campanya": "SI"},
    ])
    _create_job_for_test(app)
    with app.app_context():
        ingest_job_csv(csv_path, "testjob_lead", "admin_fincas")
        before = get_lead("p1")
        assert before["fecha_ultimo_contacto"] == ""

        update_lead_state("p1", "contactado", "test")
        after = get_lead("p1")
        assert after["fecha_ultimo_contacto"] != ""
    csv_path.unlink()


# -----------------------------------------------------------------------------
# Vista web
# -----------------------------------------------------------------------------

def test_leads_index_requires_login(client):
    r = client.get("/leads/", follow_redirects=False)
    assert r.status_code == 302
    assert "/login" in r.headers["Location"]


def test_leads_index_loads(admin_client, app):
    """La página /leads/ carga aunque no haya leads aún."""
    r = admin_client.get("/leads/")
    assert r.status_code == 200
    assert b"Leads" in r.data


def test_leads_index_shows_ingested_leads(admin_client, app):
    from webui.leads import ingest_job_csv

    csv_path = _create_test_csv([
        {"place_id": "p1", "nombre": "Bar Pepe", "email": "info@pepe.es",
         "score": "85", "apto_campanya": "SI"},
    ])
    _create_job_for_test(app)
    with app.app_context():
        ingest_job_csv(csv_path, "testjob_lead", "admin_fincas")

    r = admin_client.get("/leads/")
    body = r.data.decode()
    assert "Bar Pepe" in body
    assert "info@pepe.es" in body
    csv_path.unlink()


def test_lead_detail_404_for_unknown(admin_client):
    r = admin_client.get("/leads/nonexistent_id")
    assert r.status_code == 404


def test_lead_detail_loads(admin_client, app):
    from webui.leads import ingest_job_csv

    csv_path = _create_test_csv([
        {"place_id": "p1", "nombre": "Bar Pepe", "email": "info@pepe.es",
         "score": "85", "apto_campanya": "SI"},
    ])
    _create_job_for_test(app)
    with app.app_context():
        ingest_job_csv(csv_path, "testjob_lead", "admin_fincas")

    r = admin_client.get("/leads/p1")
    assert r.status_code == 200
    assert b"Bar Pepe" in r.data
    csv_path.unlink()


def test_update_state_via_post(admin_client, app):
    from webui.leads import ingest_job_csv, get_lead

    csv_path = _create_test_csv([
        {"place_id": "p1", "nombre": "X", "score": "85", "apto_campanya": "SI"},
    ])
    _create_job_for_test(app)
    with app.app_context():
        ingest_job_csv(csv_path, "testjob_lead", "admin_fincas")

    r = admin_client.post("/leads/p1/state", data={
        "estado": "contactado",
        "notas": "Llamé al jefe",
    }, follow_redirects=False)
    assert r.status_code == 302

    with app.app_context():
        lead = get_lead("p1")
    assert lead["estado"] == "contactado"
    assert lead["notas"] == "Llamé al jefe"
    csv_path.unlink()


def test_update_state_rejects_invalid_state(admin_client, app):
    from webui.leads import ingest_job_csv

    csv_path = _create_test_csv([
        {"place_id": "p1", "nombre": "X", "score": "85", "apto_campanya": "SI"},
    ])
    _create_job_for_test(app)
    with app.app_context():
        ingest_job_csv(csv_path, "testjob_lead", "admin_fincas")

    r = admin_client.post("/leads/p1/state", data={
        "estado": "haxx0r",
    }, follow_redirects=True)
    assert b"inv" in r.data  # mensaje de "inválido"
    csv_path.unlink()


# -----------------------------------------------------------------------------
# Filtros y búsqueda
# -----------------------------------------------------------------------------

def test_filter_by_segmento(app):
    from webui.leads import ingest_job_csv, list_leads

    csv1 = _create_test_csv([
        {"place_id": "p1", "nombre": "A", "score": "85", "apto_campanya": "SI"},
    ])
    csv2 = _create_test_csv([
        {"place_id": "p2", "nombre": "B", "score": "85", "apto_campanya": "SI"},
    ])
    _create_job_for_test(app, "j1", "admin_fincas")
    _create_job_for_test(app, "j2", "clubes_deportivos")
    with app.app_context():
        ingest_job_csv(csv1, "j1", "admin_fincas")
        ingest_job_csv(csv2, "j2", "clubes_deportivos")

    with app.app_context():
        rows_admin, _ = list_leads(segmento="admin_fincas")
        rows_clubes, _ = list_leads(segmento="clubes_deportivos")
        rows_all, total_all = list_leads()

    assert len(rows_admin) == 1 and rows_admin[0]["nombre"] == "A"
    assert len(rows_clubes) == 1 and rows_clubes[0]["nombre"] == "B"
    assert total_all == 2
    csv1.unlink()
    csv2.unlink()


def test_filter_by_estado(app):
    from webui.leads import ingest_job_csv, list_leads, update_lead_state

    csv_path = _create_test_csv([
        {"place_id": "p1", "nombre": "A", "score": "85", "apto_campanya": "SI"},
        {"place_id": "p2", "nombre": "B", "score": "85", "apto_campanya": "SI"},
    ])
    _create_job_for_test(app)
    with app.app_context():
        ingest_job_csv(csv_path, "testjob_lead", "admin_fincas")
        update_lead_state("p1", "contactado", None)

        rows_contactados, _ = list_leads(estado="contactado")
        rows_nuevos, _ = list_leads(estado="nuevo")

    assert len(rows_contactados) == 1
    assert rows_contactados[0]["place_id"] == "p1"
    assert len(rows_nuevos) == 1
    assert rows_nuevos[0]["place_id"] == "p2"
    csv_path.unlink()


def test_search_in_name_and_email(app):
    from webui.leads import ingest_job_csv, list_leads

    csv_path = _create_test_csv([
        {"place_id": "p1", "nombre": "Restaurante Madrileño",
         "email": "info@madrid.es", "score": "85", "apto_campanya": "SI"},
        {"place_id": "p2", "nombre": "Café Barcelonés",
         "email": "info@bcn.es", "score": "85", "apto_campanya": "SI"},
    ])
    _create_job_for_test(app)
    with app.app_context():
        ingest_job_csv(csv_path, "testjob_lead", "admin_fincas")
        rows1, _ = list_leads(search="Madrid")
        rows2, _ = list_leads(search="bcn")

    assert len(rows1) == 1 and rows1[0]["place_id"] == "p1"
    assert len(rows2) == 1 and rows2[0]["place_id"] == "p2"
    csv_path.unlink()


# -----------------------------------------------------------------------------
# Export CSV
# -----------------------------------------------------------------------------

def test_export_csv_downloads(admin_client, app):
    from webui.leads import ingest_job_csv

    csv_path = _create_test_csv([
        {"place_id": "p1", "nombre": "Bar X", "email": "x@x.es",
         "score": "85", "apto_campanya": "SI"},
    ])
    _create_job_for_test(app)
    with app.app_context():
        ingest_job_csv(csv_path, "testjob_lead", "admin_fincas")

    r = admin_client.get("/leads/export.csv")
    assert r.status_code == 200
    assert "text/csv" in r.headers["Content-Type"]
    body = r.data.decode()
    assert "place_id" in body  # header
    assert "Bar X" in body
    csv_path.unlink()


def test_export_excludes_contacted(admin_client, app):
    """Con exclude_contacted=1, los ya contactados no se exportan."""
    from webui.leads import ingest_job_csv, update_lead_state

    csv_path = _create_test_csv([
        {"place_id": "p1", "nombre": "A", "score": "85", "apto_campanya": "SI"},
        {"place_id": "p2", "nombre": "B", "score": "85", "apto_campanya": "SI"},
    ])
    _create_job_for_test(app)
    with app.app_context():
        ingest_job_csv(csv_path, "testjob_lead", "admin_fincas")
        update_lead_state("p1", "contactado", None)

    r = admin_client.get("/leads/export.csv?exclude_contacted=1")
    body = r.data.decode()
    # A está contactado, no debe aparecer; B sí
    assert "p2" in body
    # Comprobar que p1 no figura como fila de datos
    lines = body.strip().split("\n")
    data_lines = lines[1:]  # quitar header
    assert not any(line.startswith("p1,") for line in data_lines)
    csv_path.unlink()
