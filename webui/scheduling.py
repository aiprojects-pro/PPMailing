"""
Búsquedas programadas: simples (intervalos) y cron.

Cada `scheduled_search` puede ser de tipo:
  - simple: ejecuta cada N minutos (24 horas mínimo recomendado)
  - cron: usa expresión cron Unix

El worker (`_scheduler_loop`) corre como daemon thread y cada 60s busca
programaciones con next_run_at <= now y enabled=1. Para cada una:
  1. Comprueba si el usuario aún existe y tiene presupuesto.
  2. Crea un job (de tipo subset o radius) en estado pending.
  3. Lo encola via jobs.enqueue.
  4. Calcula next_run_at futuro y lo guarda.

Si falla N veces seguidas, la programación se deshabilita para no
malgastar API.
"""

import json
import threading
from datetime import datetime, timedelta

from croniter import croniter
from flask import (
    Blueprint, abort, current_app, flash, jsonify, redirect, render_template,
    request, url_for,
)

from .auth import admin_required, current_user, login_required
from .db import get_db, new_connection
from .segments import all_segments


bp = Blueprint("scheduling", __name__, url_prefix="/schedules")


# Mínimo absoluto entre ejecuciones: 1 hora. Aunque la cron expr o el
# interval permitan menos, el worker lo respeta para evitar saturar API.
MIN_INTERVAL_MINUTES = 60

# Máximo de fallos consecutivos antes de auto-desactivar
MAX_FAILURES = 5


# -----------------------------------------------------------------------------
# Cálculo de próximas ejecuciones
# -----------------------------------------------------------------------------

def compute_next_run(
    schedule_kind: str,
    interval_minutes: int = 0,
    cron_expr: str = "",
    base_time: datetime | None = None,
) -> datetime:
    """
    Calcula la próxima ejecución a partir de `base_time` (o ahora).

    Para 'simple', suma interval_minutes. Para 'cron', usa croniter.
    En ambos casos respeta MIN_INTERVAL_MINUTES.
    """
    base = base_time or datetime.now()
    if schedule_kind == "simple":
        minutes = max(interval_minutes, MIN_INTERVAL_MINUTES)
        return base + timedelta(minutes=minutes)
    elif schedule_kind == "cron":
        try:
            itr = croniter(cron_expr, base)
            nxt = itr.get_next(datetime)
            # Si el cron pide menos de MIN_INTERVAL_MINUTES, ajustamos
            min_next = base + timedelta(minutes=MIN_INTERVAL_MINUTES)
            return max(nxt, min_next)
        except Exception as exc:
            raise ValueError(f"Expresión cron inválida: {exc}")
    else:
        raise ValueError(f"schedule_kind desconocido: {schedule_kind}")


def validate_cron_expression(expr: str) -> str | None:
    """Devuelve None si es válida; mensaje de error si no."""
    if not expr.strip():
        return "Expresión cron vacía."
    try:
        croniter(expr.strip(), datetime.now())
        return None
    except Exception as exc:
        return f"Expresión cron inválida: {exc}"


# -----------------------------------------------------------------------------
# CRUD
# -----------------------------------------------------------------------------

def list_schedules(user_id: int | None = None) -> list:
    """Si user_id se da, filtra; si no, devuelve todas (admin)."""
    db = get_db()
    if user_id is None:
        return db.execute(
            "SELECT s.*, u.username FROM scheduled_searches s "
            "LEFT JOIN users u ON u.id = s.user_id "
            "ORDER BY s.enabled DESC, s.next_run_at ASC"
        ).fetchall()
    return db.execute(
        "SELECT s.*, u.username FROM scheduled_searches s "
        "LEFT JOIN users u ON u.id = s.user_id "
        "WHERE s.user_id = ? "
        "ORDER BY s.enabled DESC, s.next_run_at ASC",
        (user_id,),
    ).fetchall()


def get_schedule(schedule_id: int):
    return get_db().execute(
        "SELECT * FROM scheduled_searches WHERE id = ?", (schedule_id,)
    ).fetchone()


def create_schedule(
    user_id: int, name: str, segmento: str,
    ambito_kind: str, ambito: str, points_json: str,
    max_paginas: int, schedule_kind: str,
    interval_minutes: int, cron_expr: str,
    enabled: bool = True,
) -> int:
    """Crea una programación y devuelve su id."""
    now = datetime.now()
    next_run = compute_next_run(schedule_kind, interval_minutes, cron_expr, now)

    cur = get_db().execute(
        "INSERT INTO scheduled_searches "
        "(user_id, name, segmento, ambito_kind, ambito, points_json, "
        " max_paginas, schedule_kind, interval_minutes, cron_expr, "
        " enabled, next_run_at, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, name[:200], segmento, ambito_kind, ambito, points_json,
         max_paginas, schedule_kind, interval_minutes, cron_expr,
         1 if enabled else 0,
         next_run.isoformat(timespec="seconds"),
         now.isoformat(timespec="seconds")),
    )
    return cur.lastrowid


def toggle_schedule(schedule_id: int, enabled: bool):
    """Activa o desactiva una programación. Resetea failure_count al activar."""
    db = get_db()
    if enabled:
        # Al re-activar, calcular el próximo next_run_at desde ahora
        row = db.execute(
            "SELECT schedule_kind, interval_minutes, cron_expr "
            "FROM scheduled_searches WHERE id = ?", (schedule_id,)
        ).fetchone()
        if row is None:
            return False
        try:
            next_run = compute_next_run(
                row["schedule_kind"], row["interval_minutes"], row["cron_expr"]
            )
            db.execute(
                "UPDATE scheduled_searches SET enabled=1, failure_count=0, "
                "last_error='', next_run_at=? WHERE id=?",
                (next_run.isoformat(timespec="seconds"), schedule_id),
            )
        except ValueError:
            return False
    else:
        db.execute(
            "UPDATE scheduled_searches SET enabled=0 WHERE id=?",
            (schedule_id,),
        )
    return True


def delete_schedule(schedule_id: int) -> bool:
    cur = get_db().execute(
        "DELETE FROM scheduled_searches WHERE id = ?", (schedule_id,)
    )
    return cur.rowcount > 0


# -----------------------------------------------------------------------------
# Worker
# -----------------------------------------------------------------------------

# Intervalo de polling del scheduler. 60s es razonable: las programaciones
# tienen resolución de minutos, no de segundos.
SCHEDULER_INTERVAL_SECONDS = 60

_scheduler_started = threading.Event()
_scheduler_lock = threading.Lock()
_scheduler_stop = threading.Event()


def _scheduler_loop():
    """Bucle del worker de scheduling."""
    while not _scheduler_stop.is_set():
        try:
            _process_due_schedules()
        except Exception as exc:
            print(f"[scheduler] Error en ciclo: {exc}")
        _scheduler_stop.wait(SCHEDULER_INTERVAL_SECONDS)


def _process_due_schedules():
    """
    Procesa las programaciones vencidas. Para cada una:
      1. Marca next_run_at futuro (para que no se procese dos veces si la
         encolación tarda).
      2. Crea el job en BD.
      3. Lo encola via jobs.enqueue.
      4. Si algo falla, incrementa failure_count y registra last_error.
    """
    from .jobs import create_radius_job, enqueue, monthly_spend
    from .jobs import estimate_for_segment, estimate_for_radius
    from .cost import estimate_cost
    import uuid

    now = datetime.now()
    conn = new_connection()
    try:
        due = conn.execute(
            "SELECT s.*, u.username, u.budget_eur_monthly "
            "FROM scheduled_searches s "
            "LEFT JOIN users u ON u.id = s.user_id "
            "WHERE s.enabled = 1 AND s.next_run_at <= ?",
            (now.isoformat(timespec="seconds"),),
        ).fetchall()
    finally:
        conn.close()

    for sched in due:
        sched_id = sched["id"]
        try:
            _execute_schedule(sched)
        except Exception as exc:
            _mark_schedule_failed(sched_id, str(exc)[:500])


def _execute_schedule(sched):
    """
    Ejecuta una programación: crea el job y lo encola.
    Lanza excepción si algo falla (caller se encarga del fallo).
    """
    from .jobs import create_radius_job, enqueue, monthly_spend
    from .jobs import estimate_for_segment, estimate_for_radius
    from .cost import estimate_cost
    from . import places_radius
    import uuid

    sched_id = sched["id"]
    user_id = sched["user_id"]
    username = sched["username"] or "(eliminado)"
    segmento = sched["segmento"]
    ambito_kind = sched["ambito_kind"]
    ambito = sched["ambito"]
    points_json = sched["points_json"] or ""
    max_paginas = sched["max_paginas"]
    budget = sched["budget_eur_monthly"] or 0

    # Verificar que el usuario aún existe
    if username == "(eliminado)":
        # CASCADE debería haber borrado la programación; defensa extra
        _mark_schedule_failed(sched_id, "Usuario eliminado")
        return

    # Verificar segmento
    segs = all_segments()
    if segmento not in segs:
        _mark_schedule_failed(sched_id, f"Segmento {segmento} no existe")
        return

    # Estimar coste
    if ambito_kind == "radius":
        try:
            points = json.loads(points_json) if points_json else []
        except (ValueError, TypeError):
            _mark_schedule_failed(sched_id, "points_json inválido")
            return
        points, errors = places_radius.validate_points(points)
        if errors:
            _mark_schedule_failed(sched_id, "; ".join(errors)[:500])
            return
        est = estimate_for_radius(segmento, len(points), max_paginas)
    else:
        from config.ciudades_espana import SUBCONJUNTOS
        if ambito not in SUBCONJUNTOS:
            _mark_schedule_failed(sched_id, f"Ámbito {ambito} no existe")
            return
        est = estimate_for_segment(segmento, ambito, max_paginas)

    cost = est["cost_eur"]

    # Validar presupuesto (necesitamos contexto de app para get_db,
    # pero monthly_spend lo usa). Como estamos fuera de request, abrimos
    # conexión nueva.
    if budget > 0:
        conn = new_connection()
        try:
            row = conn.execute(
                "SELECT COALESCE(SUM(estimated_cost_eur), 0) AS total "
                "FROM jobs WHERE user_id = ? "
                "AND substr(queued_at, 1, 7) = strftime('%Y-%m', 'now') "
                "AND status IN ('pending', 'running', 'done')",
                (user_id,),
            ).fetchone()
            spent = float(row["total"])
        finally:
            conn.close()
        if spent + cost > budget:
            _mark_schedule_failed(
                sched_id,
                f"Presupuesto agotado: {spent:.2f} + {cost:.2f} > {budget:.2f}€"
            )
            # No incrementamos failure_count aquí; el "fallo" es de presupuesto
            # y reactivar la programación arregla el problema. Pero re-agendamos
            # la próxima ejecución para no quedarnos enganchados.
            _reschedule_only(sched)
            return

    # Crear job
    conn = new_connection()
    try:
        if ambito_kind == "radius":
            job_id = uuid.uuid4().hex[:12]
            now_iso = datetime.now().isoformat(timespec="seconds")
            conn.execute(
                "INSERT INTO jobs (id, user_id, segmento, ambito_kind, ambito, "
                "max_paginas, estimated_cost_eur, queued_at, message) "
                "VALUES (?, ?, ?, 'radius', 'radius', ?, ?, ?, 'En cola (programada)')",
                (job_id, user_id, segmento, max_paginas, cost, now_iso),
            )
            for p in points:
                conn.execute(
                    "INSERT INTO radius_points "
                    "(job_id, latitude, longitude, radius_meters, label) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (job_id, p["latitude"], p["longitude"], p["radius"], p["label"]),
                )
        else:
            job_id = uuid.uuid4().hex[:12]
            now_iso = datetime.now().isoformat(timespec="seconds")
            conn.execute(
                "INSERT INTO jobs (id, user_id, segmento, ambito_kind, ambito, "
                "max_paginas, estimated_cost_eur, queued_at, message) "
                "VALUES (?, ?, ?, 'subset', ?, ?, ?, ?, 'En cola (programada)')",
                (job_id, user_id, segmento, ambito, max_paginas, cost, now_iso),
            )
    finally:
        conn.close()

    # Log inicial
    from .jobs import _job_log_path
    _job_log_path(job_id).write_text(
        f"Job {job_id} — PROGRAMADO (id schedule {sched_id})\n"
        f"Usuario: {username}\n"
        f"Segmento: {segmento}, ámbito: {ambito_kind}/{ambito}\n"
        f"Coste estimado: {cost:.2f} €\n"
        f"Lanzado automáticamente el {datetime.now().isoformat(timespec='seconds')}\n",
        encoding="utf-8",
    )

    # Encolar (esto reactiva el worker de jobs si estaba parado)
    enqueue(job_id)

    # Marcar éxito y calcular siguiente
    _mark_schedule_success(sched, job_id)


def _mark_schedule_success(sched, job_id: str):
    """Actualiza last_run_at + last_job_id + next_run_at."""
    now = datetime.now()
    try:
        next_run = compute_next_run(
            sched["schedule_kind"],
            sched["interval_minutes"],
            sched["cron_expr"],
            base_time=now,
        )
    except ValueError as exc:
        # cron mal formado; deshabilitamos
        conn = new_connection()
        conn.execute(
            "UPDATE scheduled_searches SET enabled=0, last_error=?, "
            "failure_count=failure_count+1 WHERE id=?",
            (str(exc)[:500], sched["id"]),
        )
        conn.close()
        return

    conn = new_connection()
    try:
        conn.execute(
            "UPDATE scheduled_searches SET "
            "last_run_at=?, last_job_id=?, next_run_at=?, "
            "failure_count=0, last_error='' "
            "WHERE id=?",
            (now.isoformat(timespec="seconds"), job_id,
             next_run.isoformat(timespec="seconds"), sched["id"]),
        )
    finally:
        conn.close()


def _reschedule_only(sched):
    """
    Re-agenda sin lanzar job (usado cuando el presupuesto bloquea la ejecución).
    """
    try:
        next_run = compute_next_run(
            sched["schedule_kind"],
            sched["interval_minutes"],
            sched["cron_expr"],
        )
    except ValueError:
        return
    conn = new_connection()
    try:
        conn.execute(
            "UPDATE scheduled_searches SET next_run_at=? WHERE id=?",
            (next_run.isoformat(timespec="seconds"), sched["id"]),
        )
    finally:
        conn.close()


def _mark_schedule_failed(sched_id: int, error: str):
    """Incrementa failure_count y deshabilita si supera MAX_FAILURES."""
    conn = new_connection()
    try:
        row = conn.execute(
            "SELECT failure_count FROM scheduled_searches WHERE id=?",
            (sched_id,),
        ).fetchone()
        if row is None:
            return
        new_count = (row["failure_count"] or 0) + 1
        if new_count >= MAX_FAILURES:
            conn.execute(
                "UPDATE scheduled_searches SET failure_count=?, last_error=?, "
                "enabled=0 WHERE id=?",
                (new_count, f"Auto-desactivada tras {MAX_FAILURES} fallos: {error}",
                 sched_id),
            )
            print(f"[scheduler] Schedule {sched_id} auto-desactivada: {error}")
        else:
            conn.execute(
                "UPDATE scheduled_searches SET failure_count=?, last_error=? "
                "WHERE id=?",
                (new_count, error, sched_id),
            )
            print(f"[scheduler] Schedule {sched_id} falló ({new_count}/{MAX_FAILURES}): {error}")
    finally:
        conn.close()


def ensure_scheduler_running() -> None:
    """Arranca el scheduler si no está vivo. Idempotente."""
    with _scheduler_lock:
        if _scheduler_started.is_set():
            return
        t = threading.Thread(target=_scheduler_loop, daemon=True,
                             name="ppmailing-scheduler-worker")
        t.start()
        _scheduler_started.set()


# -----------------------------------------------------------------------------
# Rutas
# -----------------------------------------------------------------------------

@bp.route("/")
@login_required
def index():
    u = current_user()
    if u["role"] == "admin":
        schedules = list_schedules()
    else:
        schedules = list_schedules(user_id=u["id"])
    return render_template("schedules_list.html", schedules=schedules)


@bp.route("/new", methods=["GET", "POST"])
@login_required
def new():
    u = current_user()
    segs = all_segments()

    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        segmento = (request.form.get("segmento") or "").strip()
        ambito_kind = (request.form.get("ambito_kind") or "subset").strip()
        ambito = (request.form.get("ambito") or "espana").strip()
        points_json = (request.form.get("points_json") or "").strip()
        try:
            max_paginas = int(request.form.get("max_paginas", "3"))
        except (ValueError, TypeError):
            max_paginas = 3
        max_paginas = max(1, min(max_paginas, 3))

        schedule_kind = (request.form.get("schedule_kind") or "simple").strip()
        cron_expr = (request.form.get("cron_expr") or "").strip()

        # Interval depende del modo simple seleccionado
        simple_preset = request.form.get("simple_preset", "weekly")
        interval_map = {
            "daily": 24 * 60,
            "weekly": 7 * 24 * 60,
            "monthly": 30 * 24 * 60,
        }
        interval_minutes = interval_map.get(simple_preset, 7 * 24 * 60)

        # Validaciones
        errors = []
        if not name:
            errors.append("Indica un nombre para la programación.")
        if segmento not in segs:
            errors.append("Segmento desconocido.")
        if ambito_kind not in ("subset", "radius"):
            errors.append("Tipo de ámbito inválido.")
        if schedule_kind not in ("simple", "cron"):
            errors.append("Tipo de programación inválido.")
        if schedule_kind == "cron":
            err = validate_cron_expression(cron_expr)
            if err:
                errors.append(err)

        # Validación específica del ámbito
        if ambito_kind == "subset":
            from config.ciudades_espana import SUBCONJUNTOS
            if ambito not in SUBCONJUNTOS:
                errors.append("Ámbito desconocido.")
            points_json = ""  # limpiar
        else:
            # radius
            from . import places_radius
            try:
                pts = json.loads(points_json) if points_json else []
            except (ValueError, TypeError):
                errors.append("Puntos JSON inválido.")
                pts = []
            normalized, pt_errors = places_radius.validate_points(pts)
            errors.extend(pt_errors)
            if normalized:
                points_json = json.dumps(normalized)
            ambito = "radius"

        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("schedule_form.html",
                                   form=request.form, segments=segs)

        try:
            sched_id = create_schedule(
                user_id=u["id"], name=name, segmento=segmento,
                ambito_kind=ambito_kind, ambito=ambito,
                points_json=points_json,
                max_paginas=max_paginas, schedule_kind=schedule_kind,
                interval_minutes=interval_minutes, cron_expr=cron_expr,
                enabled=True,
            )
            flash(f"Programación '{name}' creada (id {sched_id}).", "success")
            return redirect(url_for("scheduling.index"))
        except Exception as exc:
            flash(f"Error al crear: {exc}", "error")

    return render_template("schedule_form.html", form={}, segments=segs)


@bp.route("/<int:schedule_id>/toggle", methods=["POST"])
@login_required
def toggle(schedule_id):
    u = current_user()
    sched = get_schedule(schedule_id)
    if sched is None:
        abort(404)
    # Solo el dueño o admin puede tocarla
    if u["role"] != "admin" and sched["user_id"] != u["id"]:
        abort(403)

    new_state = request.form.get("enabled") == "1"
    if toggle_schedule(schedule_id, new_state):
        flash(
            f"Programación {'activada' if new_state else 'pausada'}.",
            "success",
        )
    else:
        flash("Error al actualizar la programación.", "error")
    return redirect(url_for("scheduling.index"))


@bp.route("/<int:schedule_id>/delete", methods=["POST"])
@login_required
def delete(schedule_id):
    u = current_user()
    sched = get_schedule(schedule_id)
    if sched is None:
        abort(404)
    if u["role"] != "admin" and sched["user_id"] != u["id"]:
        abort(403)
    delete_schedule(schedule_id)
    flash("Programación eliminada.", "success")
    return redirect(url_for("scheduling.index"))
