"""
Job runner: cola + worker único + outputs aislados por job.

Cambios frente a la primera versión:
  - Hay UNA cola global (`queue.Queue`) procesada por UN worker (daemon thread).
    Los jobs se procesan en orden FIFO; nunca corren dos en paralelo. Esto
    evita saturar los rate-limits de Google Places y de las webs scrapeadas.
  - Los outputs intermedios y finales de cada job se mueven a
    `webui/instance/job_outputs/<job_id>/` cuando termina. Así dos jobs del
    mismo segmento+ámbito en el mismo día no se pisan.
  - Cada update a la BD usa una conexión nueva del worker (no `g.db`, que
    está atado al contexto de request).
  - El estado 'pending' marca jobs en cola; 'running' los que se están
    ejecutando ya.
"""

import csv
import os
import queue
import shutil
import subprocess
import sys
import threading
import uuid
from datetime import datetime
from pathlib import Path

from flask import (
    Blueprint, abort, flash, jsonify, redirect, render_template, request,
    send_from_directory, url_for,
)

from .auth import current_user, login_required
from .cost import estimate_cost
from .db import get_db, new_connection
from . import paths as _paths
from .paths import DATA_DIR, PROJECT_ROOT
from .segments import BUILTIN_SEGMENTS, all_segments, get_custom_segment
from .settings import get_api_key, get_retention

# Importar config compartida
sys.path.insert(0, str(PROJECT_ROOT))
from config.ciudades_espana import SUBCONJUNTOS  # noqa: E402


bp = Blueprint("jobs", __name__)


# -----------------------------------------------------------------------------
# Cola + worker
# -----------------------------------------------------------------------------

_job_queue: "queue.Queue[str]" = queue.Queue()
_worker_started = threading.Event()
_worker_lock = threading.Lock()


def ensure_worker_running() -> None:
    """Arranca el worker si no está vivo. Idempotente y thread-safe."""
    with _worker_lock:
        if _worker_started.is_set():
            return
        t = threading.Thread(target=_worker_loop, daemon=True,
                             name="ppmailing-jobs-worker")
        t.start()
        _worker_started.set()


def _worker_loop():
    """Bucle infinito del worker. Toma un job_id y lo ejecuta."""
    while True:
        job_id = _job_queue.get()
        try:
            _run_job_pipeline(job_id)
        except Exception as exc:
            # Última red de seguridad: cualquier excepción no capturada
            # se refleja como error en la BD.
            _update_job(
                job_id, status="error", step="error",
                message=f"Excepción no controlada: {exc}",
                finished_at=datetime.now().isoformat(timespec="seconds"),
            )
            _append_log(job_id, f"\nFATAL: {exc}\n")
        finally:
            _job_queue.task_done()


def enqueue(job_id: str) -> None:
    from flask import current_app
    try:
        is_testing = current_app.config.get("TESTING", False)
    except RuntimeError:
        # Fuera de contexto Flask (worker, etc.)
        is_testing = False
    if not is_testing:
        ensure_worker_running()
    _job_queue.put(job_id)


def queue_size() -> int:
    return _job_queue.qsize()


# -----------------------------------------------------------------------------
# Helpers de BD para el worker (conexión propia, no g.db)
# -----------------------------------------------------------------------------

def _update_job(job_id: str, **fields):
    conn = new_connection()
    try:
        keys = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [job_id]
        conn.execute(f"UPDATE jobs SET {keys} WHERE id = ?", values)
    finally:
        conn.close()


def _job_log_path(job_id: str) -> Path:
    return _paths.JOB_LOG_DIR / f"{job_id}.log"


def _job_output_dir(job_id: str) -> Path:
    p = _paths.JOB_OUTPUTS_DIR / job_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def _append_log(job_id: str, line: str):
    with open(_job_log_path(job_id), "a", encoding="utf-8") as f:
        f.write(line if line.endswith("\n") else line + "\n")


# -----------------------------------------------------------------------------
# Inyección de segmentos personalizados al subproceso
# -----------------------------------------------------------------------------

def _prepare_extra_segments_file(segmento_id: str) -> Path | None:
    """Si el segmento es custom, lo materializa en JSON para run_with_extras."""
    if segmento_id in BUILTIN_SEGMENTS:
        return None
    custom = get_custom_segment(segmento_id)
    if custom is None:
        return None
    # Nombre del archivo basado SOLO en el job, no en segmento_id (defensa
    # extra contra path traversal aunque la validación ya lo impide).
    path = _paths.EXTRA_SEGMENTS_DIR / f"extra_{uuid.uuid4().hex[:8]}.json"
    import json as _json
    with open(path, "w", encoding="utf-8") as f:
        _json.dump({segmento_id: custom}, f, ensure_ascii=False)
    return path


def _segment_queries(segmento_id: str) -> list[str]:
    """Obtiene la lista de queries de un segmento (built-in o custom)."""
    if segmento_id in BUILTIN_SEGMENTS:
        return list(BUILTIN_SEGMENTS[segmento_id].get("queries", []))
    custom = get_custom_segment(segmento_id)
    if custom is None:
        return []
    return list(custom.get("queries", []))


# -----------------------------------------------------------------------------
# Ejecución de scripts
# -----------------------------------------------------------------------------

def _run_script(job_id: str, args: list, env: dict, step_label: str) -> int:
    _append_log(job_id, f"\n===== {step_label} =====")
    _append_log(job_id, f"CMD: {' '.join(args)}")

    python_bin = args[0]
    rest = args[1:]
    extra_flags = []
    while rest and rest[0].startswith("-") and rest[0] != "-":
        extra_flags.append(rest.pop(0))
    wrapped = [python_bin] + extra_flags + ["-m", "webui.run_with_extras"] + rest

    proc = subprocess.Popen(
        wrapped, cwd=str(PROJECT_ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        _append_log(job_id, line.rstrip("\n"))
    proc.wait()
    _append_log(job_id, f"\n[exit code: {proc.returncode}]")
    return proc.returncode


def _run_job_pipeline(job_id: str):
    """Ejecuta los 3 pasos del pipeline para un job."""
    conn = new_connection()
    try:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        return
    if row["status"] != "pending":
        # Ya procesado o cancelado
        return

    segmento = row["segmento"]
    ambito = row["ambito"]
    ambito_kind = row["ambito_kind"] if "ambito_kind" in row.keys() else "subset"
    max_paginas = row["max_paginas"]
    now = datetime.now().isoformat(timespec="seconds")

    # API key
    api_key = get_api_key()
    if not api_key:
        _update_job(job_id, status="error", step="error",
                    message="No hay clave de Google Places configurada.",
                    started_at=now, finished_at=now)
        _append_log(job_id, "ERROR: no hay clave de Google Places configurada.")
        return

    python_bin = sys.executable
    fecha = datetime.now().strftime("%Y%m%d")

    # Archivos esperados (donde los escriben los scripts originales)
    # Para radio, el output_json lo escribimos nosotros con el mismo formato.
    if ambito_kind == "radius":
        # Nombre único: incluimos job_id para evitar colisión si dos jobs
        # de radio se solapan el mismo día con mismo segmento.
        source_json = DATA_DIR / f"{segmento}_radius_{job_id}_{fecha}.json"
        source_enriched = DATA_DIR / f"enriquecido_{segmento}_radius_{job_id}_{fecha}.json"
        source_csv = DATA_DIR / f"leads_{segmento}_radius_{job_id}_{fecha}.csv"
    else:
        source_json = DATA_DIR / f"{segmento}_{ambito}_{fecha}.json"
        source_enriched = DATA_DIR / f"enriquecido_{segmento}_{ambito}_{fecha}.json"
        source_csv = DATA_DIR / f"leads_{segmento}_{ambito}_{fecha}.csv"

    # Donde MOVEMOS los outputs al final para aislarlos
    job_dir = _job_output_dir(job_id)

    try:
        # Paso 1 — RAMA: radio vs subconjunto
        _update_job(job_id, status="running", step="buscar", progress=5,
                    message="Buscando negocios en Google Places...",
                    started_at=now)

        if ambito_kind == "radius":
            # Búsqueda por puntos. Reutilizamos in-process el módulo.
            from . import places_radius
            # Cargar puntos y queries
            conn = new_connection()
            try:
                pts = conn.execute(
                    "SELECT latitude, longitude, radius_meters, label "
                    "FROM radius_points WHERE job_id = ?",
                    (job_id,),
                ).fetchall()
            finally:
                conn.close()
            points = [
                {"latitude": p["latitude"], "longitude": p["longitude"],
                 "radius": p["radius_meters"], "label": p["label"]}
                for p in pts
            ]
            # Obtener queries del segmento
            queries = _segment_queries(segmento)
            if not queries:
                return _finish_error(job_id, "Segmento sin queries", -1)
            if not points:
                return _finish_error(job_id, "Job de radio sin puntos definidos", -1)

            _append_log(
                job_id,
                f"\n===== PASO 1/3 — búsqueda por radio (in-process) =====",
            )
            _append_log(
                job_id,
                f"Puntos: {len(points)} · Queries: {len(queries)} · "
                f"Páginas/query: {max_paginas}",
            )

            def _on_progress(current, total, msg):
                _append_log(job_id, msg)
                # Actualizar progress de la barra entre 5 y 30%
                prog = 5 + int(25 * current / total)
                _update_job(job_id, progress=prog)

            try:
                stats = places_radius.run_radius_search(
                    segmento_id=segmento,
                    queries=queries,
                    points=points,
                    api_key=api_key,
                    max_paginas=max_paginas,
                    output_json=source_json,
                    progress_callback=_on_progress,
                )
                _append_log(
                    job_id,
                    f"Búsqueda por radio completada: "
                    f"{stats['total_calls']} llamadas, "
                    f"{stats['total_results']} resultados brutos, "
                    f"{stats['unique_results']} únicos.",
                )
                if stats["errors"]:
                    _append_log(job_id, f"Errores parciales: {len(stats['errors'])}")
                    for err in stats["errors"][:10]:
                        _append_log(job_id, f"  - {err}")
            except Exception as exc:
                _append_log(job_id, f"FATAL: {exc}")
                return _finish_error(job_id, f"Fallo en búsqueda por radio: {exc}", -1)

        else:
            # Rama clásica: ámbito por subconjunto, vía buscar.py
            # Entorno
            env = os.environ.copy()
            env["GOOGLE_PLACES_API_KEY"] = api_key
            env["PYTHONPATH"] = str(PROJECT_ROOT)
            extras_file = _prepare_extra_segments_file(segmento)
            if extras_file is not None:
                env["PPM_EXTRA_SEGMENTS"] = str(extras_file)

            rc = _run_script(
                job_id,
                [python_bin, "-u", "scripts/buscar.py",
                 "--segmento", segmento, "--ambito", ambito,
                 "--max-paginas", str(max_paginas)],
                env, "PASO 1/3 — buscar.py",
            )
            if rc != 0 or not source_json.exists():
                return _finish_error(job_id, "Fallo en búsqueda", rc)
            # Limpiar extras file si lo creamos
            if extras_file is not None and extras_file.exists():
                try:
                    extras_file.unlink()
                except OSError:
                    pass

        # Entorno para los pasos 2-3 (común a ambas ramas)
        env = os.environ.copy()
        env["GOOGLE_PLACES_API_KEY"] = api_key
        env["PYTHONPATH"] = str(PROJECT_ROOT)

        # Mover el JSON al directorio del job
        target_json = job_dir / source_json.name
        shutil.copy2(source_json, target_json)
        _update_job(job_id, json_filename=source_json.name, progress=35,
                    message="Búsqueda completada. Extrayendo emails...")

        # Paso 2
        _update_job(job_id, step="extraer_emails", progress=40)
        rc = _run_script(
            job_id,
            [python_bin, "-u", "scripts/extraer_emails.py",
             "--input", source_json.name],
            env, "PASO 2/3 — extraer_emails.py",
        )
        if rc != 0 or not source_enriched.exists():
            return _finish_error(job_id, "Fallo en extracción de emails", rc)

        target_enriched = job_dir / source_enriched.name
        shutil.copy2(source_enriched, target_enriched)
        _update_job(job_id, enriched_filename=source_enriched.name, progress=75,
                    message="Emails extraídos. Generando CSV final...")

        # Paso 3
        _update_job(job_id, step="generar_csv", progress=80)
        rc = _run_script(
            job_id,
            [python_bin, "-u", "scripts/generar_csv.py",
             "--input", source_enriched.name],
            env, "PASO 3/3 — generar_csv.py",
        )
        if rc != 0 or not source_csv.exists():
            return _finish_error(job_id, "Fallo en generación de CSV", rc)

        target_csv = job_dir / source_csv.name
        shutil.copy2(source_csv, target_csv)

        # Conteos
        total, aptos = _count_leads(target_csv)

        # Volcar a leads_master (deduplicado por place_id)
        ingest_stats = {"nuevos": 0, "actualizados": 0, "total": 0}
        try:
            from .leads import ingest_job_csv
            ingest_stats = ingest_job_csv(target_csv, job_id, segmento)
        except Exception as exc:
            # No tirar el job entero por un fallo de la ingesta; lo logueamos
            # y dejamos el CSV intacto (siempre se puede re-ingestar después).
            _append_log(job_id, f"\nWARN: error al volcar a leads_master: {exc}")

        msg = (f"Completado. {total} leads, {aptos} aptos. "
               f"{ingest_stats['nuevos']} nuevos en BD, "
               f"{ingest_stats['actualizados']} ya conocidos.")
        _update_job(
            job_id, status="done", step="done", progress=100,
            csv_filename=source_csv.name,
            total_leads=total, aptos_leads=aptos,
            message=msg,
            finished_at=datetime.now().isoformat(timespec="seconds"),
        )
    except Exception as exc:
        _append_log(job_id, f"\nFATAL: {exc}")
        return _finish_error(job_id, f"Excepción no controlada: {exc}", -1)


def _finish_error(job_id: str, msg: str, rc: int):
    _update_job(
        job_id, status="error", step="error",
        message=f"{msg} (exit {rc}).",
        finished_at=datetime.now().isoformat(timespec="seconds"),
    )


def _count_leads(csv_path: Path) -> tuple[int, int]:
    total = aptos = 0
    try:
        with open(csv_path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                total += 1
                if (row.get("apto_campanya") or "").upper() == "SI":
                    aptos += 1
    except OSError:
        pass
    return total, aptos


# -----------------------------------------------------------------------------
# Cálculos de coste / consumo mensual
# -----------------------------------------------------------------------------

def estimate_for_segment(segmento_id: str, ambito: str, max_paginas: int) -> dict:
    segs = all_segments()
    if segmento_id not in segs:
        return {"text_searches": 0, "cost_eur": 0, "cost_usd": 0, "warnings": []}
    num_queries = len(segs[segmento_id]["queries"])
    num_ciudades = len(SUBCONJUNTOS.get(ambito, []))
    return estimate_cost(num_ciudades, num_queries, max_paginas)


def monthly_spend(user_id: int) -> float:
    """Suma del coste estimado de jobs del usuario en el mes actual."""
    conn = get_db()
    row = conn.execute(
        "SELECT COALESCE(SUM(estimated_cost_eur), 0) AS total "
        "FROM jobs WHERE user_id = ? "
        "AND substr(queued_at, 1, 7) = strftime('%Y-%m', 'now') "
        "AND status IN ('pending', 'running', 'done')",
        (user_id,),
    ).fetchone()
    return float(row["total"])


# -----------------------------------------------------------------------------
# Rutas
# -----------------------------------------------------------------------------

@bp.route("/jobs/estimate")
@login_required
def estimate():
    """API GET para que el formulario pre-muestre el coste antes de enviar."""
    segmento = request.args.get("segmento", "")
    ambito = request.args.get("ambito", "espana")
    try:
        max_paginas = max(1, min(int(request.args.get("max_paginas", "3")), 3))
    except (ValueError, TypeError):
        max_paginas = 3
    est = estimate_for_segment(segmento, ambito, max_paginas)
    return jsonify(est)


@bp.route("/jobs/new", methods=["POST"])
@login_required
def new_job():
    u = current_user()
    segmento = (request.form.get("segmento") or "").strip()
    ambito = (request.form.get("ambito") or "espana").strip()
    try:
        max_paginas = int(request.form.get("max_paginas", "3"))
    except (ValueError, TypeError):
        max_paginas = 3
    max_paginas = max(1, min(max_paginas, 3))
    confirm_high_cost = request.form.get("confirm_high_cost") == "1"

    segs = all_segments()
    if segmento not in segs:
        flash("Segmento desconocido.", "error")
        return redirect(url_for("dashboard.index"))
    if ambito not in SUBCONJUNTOS:
        flash("Ámbito desconocido.", "error")
        return redirect(url_for("dashboard.index"))
    if not get_api_key():
        flash("No hay clave de Google Places configurada. Avisa al admin.", "error")
        return redirect(url_for("dashboard.index"))

    est = estimate_for_segment(segmento, ambito, max_paginas)
    cost = est["cost_eur"]

    # Requerir confirmación explícita si el coste es alto
    if cost >= 20 and not confirm_high_cost:
        flash(
            f"Esta búsqueda costará ~{cost:.2f} €. "
            "Marca la casilla de confirmación si estás seguro.",
            "error",
        )
        return redirect(url_for("dashboard.index"))

    # Validar presupuesto del usuario
    if u["budget_eur_monthly"] and u["budget_eur_monthly"] > 0:
        spent = monthly_spend(u["id"])
        if spent + cost > u["budget_eur_monthly"]:
            flash(
                f"Esta búsqueda superaría tu presupuesto mensual "
                f"({spent:.2f} € gastados + {cost:.2f} € nuevos > "
                f"{u['budget_eur_monthly']:.2f} €).",
                "error",
            )
            return redirect(url_for("dashboard.index"))

    # Crear job
    job_id = uuid.uuid4().hex[:12]
    now = datetime.now().isoformat(timespec="seconds")
    get_db().execute(
        "INSERT INTO jobs (id, user_id, segmento, ambito_kind, ambito, "
        "max_paginas, estimated_cost_eur, queued_at, message) "
        "VALUES (?, ?, ?, 'subset', ?, ?, ?, ?, 'En cola')",
        (job_id, u["id"], segmento, ambito, max_paginas, cost, now),
    )

    # Log inicial
    _job_log_path(job_id).write_text(
        f"Job {job_id} — {segmento} / {ambito} / max_paginas={max_paginas}\n"
        f"Lanzado por {u['username']} el {now}\n"
        f"Coste estimado: {cost:.2f} € ({est['text_searches']} text searches)\n"
        f"En cola (puede haber otros jobs antes).\n",
        encoding="utf-8",
    )

    enqueue(job_id)
    flash(
        f"Búsqueda en cola (id: {job_id}, estimado: {cost:.2f} €).",
        "success",
    )
    return redirect(url_for("jobs.detail", job_id=job_id))


def estimate_for_radius(segmento_id: str, num_points: int, max_paginas: int) -> dict:
    """Estimación de coste para búsqueda por radio."""
    segs = all_segments()
    if segmento_id not in segs:
        return {"text_searches": 0, "cost_eur": 0, "cost_usd": 0, "warnings": []}
    num_queries = len(segs[segmento_id]["queries"])
    return estimate_cost(num_points, num_queries, max_paginas)


def create_radius_job(
    user_id: int, segmento: str, max_paginas: int,
    points: list[dict], estimated_cost_eur: float,
    username: str,
) -> str:
    """
    Crea un job de tipo radius y sus filas en radius_points.
    Devuelve el job_id. NO encola (lo hace el caller).
    """
    job_id = uuid.uuid4().hex[:12]
    now = datetime.now().isoformat(timespec="seconds")
    db = get_db()
    db.execute(
        "INSERT INTO jobs (id, user_id, segmento, ambito_kind, ambito, "
        "max_paginas, estimated_cost_eur, queued_at, message) "
        "VALUES (?, ?, ?, 'radius', 'radius', ?, ?, ?, 'En cola')",
        (job_id, user_id, segmento, max_paginas, estimated_cost_eur, now),
    )
    for p in points:
        db.execute(
            "INSERT INTO radius_points "
            "(job_id, latitude, longitude, radius_meters, label) "
            "VALUES (?, ?, ?, ?, ?)",
            (job_id, p["latitude"], p["longitude"], p["radius"], p["label"]),
        )

    # Log inicial
    points_summary = ", ".join(
        f"{p['label']}({p['latitude']:.4f},{p['longitude']:.4f},{p['radius']}m)"
        for p in points[:5]
    )
    if len(points) > 5:
        points_summary += f" ...(+{len(points) - 5} más)"
    _job_log_path(job_id).write_text(
        f"Job {job_id} — RADIO — {segmento}\n"
        f"Puntos: {len(points)}\n"
        f"{points_summary}\n"
        f"max_paginas={max_paginas}\n"
        f"Lanzado por {username} el {now}\n"
        f"Coste estimado: {estimated_cost_eur:.2f} €\n",
        encoding="utf-8",
    )
    return job_id


@bp.route("/jobs/new-radius", methods=["POST"])
@login_required
def new_radius_job():
    u = current_user()
    segmento = (request.form.get("segmento") or "").strip()
    try:
        max_paginas = int(request.form.get("max_paginas", "3"))
    except (ValueError, TypeError):
        max_paginas = 3
    max_paginas = max(1, min(max_paginas, 3))
    confirm_high_cost = request.form.get("confirm_high_cost") == "1"

    # Parsear puntos del form: vienen como puntos[i][lat], puntos[i][lng], etc.
    # Para simplificar, pasamos un único campo JSON 'points_json' desde la UI.
    points_raw = request.form.get("points_json", "").strip()
    try:
        import json as _json
        points_in = _json.loads(points_raw) if points_raw else []
    except (ValueError, TypeError):
        flash("Formato de puntos inválido.", "error")
        return redirect(url_for("dashboard.index"))

    # Validar puntos
    from . import places_radius
    points, errors = places_radius.validate_points(points_in)
    if errors:
        for e in errors:
            flash(e, "error")
        return redirect(url_for("dashboard.index"))

    segs = all_segments()
    if segmento not in segs:
        flash("Segmento desconocido.", "error")
        return redirect(url_for("dashboard.index"))
    if not get_api_key():
        flash("No hay clave de Google Places configurada. Avisa al admin.", "error")
        return redirect(url_for("dashboard.index"))

    est = estimate_for_radius(segmento, len(points), max_paginas)
    cost = est["cost_eur"]

    if cost >= 20 and not confirm_high_cost:
        flash(
            f"Esta búsqueda costará ~{cost:.2f} €. "
            "Marca la casilla de confirmación si estás seguro.",
            "error",
        )
        return redirect(url_for("dashboard.index"))

    if u["budget_eur_monthly"] and u["budget_eur_monthly"] > 0:
        spent = monthly_spend(u["id"])
        if spent + cost > u["budget_eur_monthly"]:
            flash(
                f"Esta búsqueda superaría tu presupuesto mensual "
                f"({spent:.2f} € gastados + {cost:.2f} € nuevos > "
                f"{u['budget_eur_monthly']:.2f} €).",
                "error",
            )
            return redirect(url_for("dashboard.index"))

    job_id = create_radius_job(
        user_id=u["id"], segmento=segmento, max_paginas=max_paginas,
        points=points, estimated_cost_eur=cost, username=u["username"],
    )
    enqueue(job_id)
    flash(
        f"Búsqueda por radio en cola (id: {job_id}, "
        f"{len(points)} puntos, estimado: {cost:.2f} €).",
        "success",
    )
    return redirect(url_for("jobs.detail", job_id=job_id))


@bp.route("/jobs/estimate-radius")
@login_required
def estimate_radius_route():
    """API GET: estimar coste de una búsqueda por radio."""
    segmento = request.args.get("segmento", "")
    try:
        num_points = max(1, int(request.args.get("num_points", "1")))
    except (ValueError, TypeError):
        num_points = 1
    try:
        max_paginas = max(1, min(int(request.args.get("max_paginas", "3")), 3))
    except (ValueError, TypeError):
        max_paginas = 3
    est = estimate_for_radius(segmento, num_points, max_paginas)
    return jsonify(est)


@bp.route("/jobs/<job_id>")
@login_required
def detail(job_id):
    u = current_user()
    row = get_db().execute(
        "SELECT j.*, us.username FROM jobs j "
        "JOIN users us ON us.id = j.user_id WHERE j.id = ?",
        (job_id,),
    ).fetchone()
    if row is None:
        abort(404)
    if u["role"] != "admin" and row["user_id"] != u["id"]:
        abort(403)
    queue_pos = _queue_position(job_id) if row["status"] == "pending" else None
    return render_template("job_detail.html", job=row, queue_pos=queue_pos)


@bp.route("/api/jobs/<job_id>")
@login_required
def api_status(job_id):
    u = current_user()
    row = get_db().execute(
        "SELECT * FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    if row is None:
        return jsonify({"error": "not found"}), 404
    if u["role"] != "admin" and row["user_id"] != u["id"]:
        return jsonify({"error": "forbidden"}), 403

    log_text = ""
    log_path = _job_log_path(job_id)
    if log_path.exists():
        # Últimas 300 líneas
        data = log_path.read_text(encoding="utf-8", errors="replace")
        log_text = "\n".join(data.splitlines()[-300:])

    return jsonify({
        "id": row["id"],
        "status": row["status"],
        "step": row["step"],
        "progress": row["progress"],
        "message": row["message"],
        "total_leads": row["total_leads"],
        "aptos_leads": row["aptos_leads"],
        "estimated_cost_eur": row["estimated_cost_eur"],
        "has_csv": bool(row["csv_filename"]),
        "has_json": bool(row["json_filename"]),
        "has_enriched": bool(row["enriched_filename"]),
        "queue_position": _queue_position(job_id) if row["status"] == "pending" else None,
        "log": log_text,
    })


@bp.route("/download/<job_id>/<which>")
@login_required
def download(job_id, which):
    """Descarga un archivo del directorio aislado del job.

    No leemos paths de la BD para no exponernos a path traversal; en lugar
    de eso, construimos la ruta a partir de filename + job_id, y validamos
    con send_from_directory que no se salga de su carpeta.
    """
    u = current_user()
    row = get_db().execute(
        "SELECT * FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    if row is None:
        abort(404)
    if u["role"] != "admin" and row["user_id"] != u["id"]:
        abort(403)
    fname_map = {
        "csv": row["csv_filename"],
        "json": row["json_filename"],
        "enriched": row["enriched_filename"],
    }
    fname = fname_map.get(which)
    if not fname:
        abort(404)
    job_dir = _job_output_dir(job_id)
    # send_from_directory ya bloquea path traversal sobre `fname`
    return send_from_directory(str(job_dir), fname, as_attachment=True)


def _queue_position(job_id: str) -> int | None:
    """Posición aproximada en la cola (0 = siguiente). None si no está en cola."""
    # Leemos el snapshot de la queue interna. No es 100% exacto si el worker
    # acaba de hacer get(), pero suficiente para la UI.
    try:
        items = list(_job_queue.queue)
    except Exception:
        return None
    try:
        return items.index(job_id)
    except ValueError:
        return None


# -----------------------------------------------------------------------------
# Borrado de jobs (manual + automático por retención)
# -----------------------------------------------------------------------------

def delete_job_files(job_id: str) -> None:
    """Borra los archivos en disco asociados a un job. Idempotente.

    Borra:
      - webui/instance/job_outputs/<job_id>/  (toda la carpeta)
      - webui/instance/job_logs/<job_id>.log

    No toca la BD: eso lo hace _delete_job_row().
    """
    job_dir = _paths.JOB_OUTPUTS_DIR / job_id
    if job_dir.exists() and job_dir.is_dir():
        try:
            shutil.rmtree(job_dir)
        except OSError:
            pass
    log = _paths.JOB_LOG_DIR / f"{job_id}.log"
    if log.exists():
        try:
            log.unlink()
        except OSError:
            pass


def _delete_job_row(job_id: str) -> bool:
    """Borra la fila del job en la BD. Devuelve True si existía."""
    conn = new_connection()
    try:
        cur = conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        return cur.rowcount > 0
    finally:
        conn.close()


def delete_job(job_id: str) -> bool:
    """Borra completamente un job: BD + archivos. Idempotente."""
    existed = _delete_job_row(job_id)
    delete_job_files(job_id)
    return existed


def cleanup_old_jobs(retention_days: int) -> int:
    """
    Borra todos los jobs (BD + archivos) cuyo `queued_at` sea más antiguo
    que `retention_days` días. Devuelve cuántos jobs se borraron.

    Sólo borra jobs en estado terminal (done, error, cancelled) para no
    interferir con jobs en cola o en ejecución.
    """
    if retention_days < 1:
        return 0
    conn = new_connection()
    try:
        # SQLite: 'now', '-N days' calcula la fecha límite
        rows = conn.execute(
            "SELECT id FROM jobs "
            "WHERE queued_at < datetime('now', ?) "
            "AND status IN ('done', 'error', 'cancelled')",
            (f"-{retention_days} days",),
        ).fetchall()
        ids = [r["id"] for r in rows]
    finally:
        conn.close()

    for job_id in ids:
        delete_job(job_id)
    return len(ids)


# Worker periódico de retención
_retention_started = threading.Event()
_retention_lock = threading.Lock()
_retention_stop = threading.Event()

# Intervalo entre pasadas de limpieza. 1 hora es más que suficiente: la
# retención se mide en días, no en minutos.
RETENTION_INTERVAL_SECONDS = 3600


def _retention_loop():
    """Bucle del worker de retención. Comprueba cada hora si hay que limpiar."""
    while not _retention_stop.is_set():
        try:
            cfg = get_retention()
            if cfg["enabled"]:
                n = cleanup_old_jobs(cfg["days"])
                if n > 0:
                    # Log a stderr; no podemos usar app.logger fuera del contexto
                    print(f"[retention] Borrados {n} jobs antiguos "
                          f"(>{cfg['days']} días).")
        except Exception as exc:
            print(f"[retention] Error en limpieza: {exc}")
        # Esperar el intervalo, pero responder rápido si nos piden parar
        _retention_stop.wait(RETENTION_INTERVAL_SECONDS)


def ensure_retention_worker_running() -> None:
    """Arranca el worker de retención si no está vivo. Idempotente."""
    with _retention_lock:
        if _retention_started.is_set():
            return
        t = threading.Thread(target=_retention_loop, daemon=True,
                             name="ppmailing-retention-worker")
        t.start()
        _retention_started.set()


# -----------------------------------------------------------------------------
# Endpoint: borrar un job
# -----------------------------------------------------------------------------

@bp.route("/jobs/<job_id>/delete", methods=["POST"])
@login_required
def delete(job_id):
    """Borra un job. Solo el dueño del job o un admin pueden hacerlo."""
    u = current_user()
    row = get_db().execute(
        "SELECT user_id, status FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    if row is None:
        abort(404)
    if u["role"] != "admin" and row["user_id"] != u["id"]:
        abort(403)
    # No permitir borrar jobs que están corriendo: podríamos dejar al worker
    # escribiendo en una carpeta ya borrada.
    if row["status"] in ("running", "pending"):
        flash("No se puede borrar un job en curso o en cola. "
              "Espera a que termine.", "error")
        return redirect(url_for("jobs.detail", job_id=job_id))

    delete_job(job_id)
    flash(f"Búsqueda {job_id} eliminada.", "success")
    return redirect(url_for("dashboard.index"))
