"""
Leads master: deduplicación, estados, validación.

Cada job aporta sus leads. Si un lead (identificado por place_id) ya existe
en la BD por una búsqueda anterior, se actualizan los campos más recientes
y se incrementa `times_seen`. Si es nuevo, se inserta con estado 'nuevo'.

La relación N:N `lead_jobs` permite saber por qué jobs ha pasado un lead.
"""

import csv
import io
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from flask import (
    Blueprint, Response, abort, flash, jsonify, redirect, render_template,
    request, url_for,
)

from .auth import current_user, login_required
from .db import get_db, new_connection
from .email_validation import check_email_mx


bp = Blueprint("leads", __name__, url_prefix="/leads")


# Estados permitidos. Los CHECK del esquema los garantizan a nivel BD;
# aquí lo replicamos para validación temprana.
ESTADOS_VALIDOS = ("nuevo", "contactado", "respondio", "descartado")
EMAIL_STATUS_VALIDOS = ("", "mx_ok", "mx_fail", "invalid", "verified")


# -----------------------------------------------------------------------------
# Ingesta: volcado del CSV de un job a leads_master
# -----------------------------------------------------------------------------

def ingest_job_csv(csv_path: Path, job_id: str, segmento: str) -> dict:
    """
    Lee el CSV final de un job y lo vuelca a `leads_master`, deduplicando por
    place_id. Si un lead ya existe, se actualiza con los datos del CSV nuevo.

    El usuario no pierde nunca el estado (`estado`, `notas`,
    `fecha_ultimo_contacto`) ni la validación de email: esos campos
    pertenecen al lead, no al job. Si Alice contacta un lead que sale en
    el job A y luego ese mismo lead aparece en el job B, el estado
    "contactado" se conserva.

    Devuelve un dict con estadísticas: {nuevos, actualizados, total}.
    """
    if not csv_path.exists():
        return {"nuevos": 0, "actualizados": 0, "total": 0}

    nuevos = actualizados = total = 0
    now = datetime.now().isoformat(timespec="seconds")

    conn = new_connection()
    try:
        with open(csv_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                place_id = (row.get("place_id") or "").strip()
                if not place_id:
                    # Sin place_id no podemos deduplicar; saltamos.
                    continue
                total += 1

                # Construir el record desde el CSV
                lead_data = {
                    "place_id": place_id,
                    "segmento": segmento,
                    "nombre": (row.get("nombre") or "")[:500],
                    "email": (row.get("email") or "").strip().lower()[:254],
                    "telefono": (row.get("telefono") or "")[:50],
                    "web": (row.get("web") or "")[:500],
                    "direccion": (row.get("direccion") or "")[:500],
                    "localidad": (row.get("localidad") or "")[:200],
                    "provincia": (row.get("provincia") or "")[:200],
                    "ccaa": (row.get("ccaa") or "")[:200],
                    "rating": _to_float(row.get("rating")),
                    "num_resenas": _to_int(row.get("num_resenas")),
                    "score": _to_int(row.get("score")) or 0,
                    "apto_campanya": 1 if (row.get("apto_campanya") or "").upper() == "SI" else 0,
                }

                # ¿Existe ya?
                existing = conn.execute(
                    "SELECT place_id, email, email_status FROM leads_master "
                    "WHERE place_id = ?",
                    (place_id,),
                ).fetchone()

                if existing is None:
                    # Insertar nuevo
                    conn.execute(
                        "INSERT INTO leads_master "
                        "(place_id, segmento, nombre, email, telefono, web, "
                        " direccion, localidad, provincia, ccaa, rating, num_resenas, "
                        " score, apto_campanya, "
                        " first_seen_at, last_seen_at, last_seen_job_id, times_seen) "
                        "VALUES (:place_id, :segmento, :nombre, :email, :telefono, :web, "
                        " :direccion, :localidad, :provincia, :ccaa, :rating, :num_resenas, "
                        " :score, :apto_campanya, "
                        f" '{now}', '{now}', '{job_id}', 1)",
                        lead_data,
                    )
                    nuevos += 1
                else:
                    # Actualizar campos del lead pero conservar estado/notas
                    # y la validación de email si el email no ha cambiado.
                    new_email = lead_data["email"]
                    preserve_email_status = (
                        new_email == (existing["email"] or "")
                    )
                    if preserve_email_status:
                        # Mantener email_status y email_checked_at
                        conn.execute(
                            "UPDATE leads_master SET "
                            "nombre=:nombre, telefono=:telefono, web=:web, "
                            "direccion=:direccion, localidad=:localidad, "
                            "provincia=:provincia, ccaa=:ccaa, "
                            "rating=:rating, num_resenas=:num_resenas, "
                            "score=:score, apto_campanya=:apto_campanya, "
                            "segmento=:segmento, "
                            f"last_seen_at='{now}', "
                            f"last_seen_job_id='{job_id}', "
                            "times_seen = times_seen + 1 "
                            "WHERE place_id=:place_id",
                            lead_data,
                        )
                    else:
                        # El email ha cambiado -> resetear su validación
                        conn.execute(
                            "UPDATE leads_master SET "
                            "nombre=:nombre, email=:email, "
                            "email_status='', email_checked_at='', "
                            "telefono=:telefono, web=:web, "
                            "direccion=:direccion, localidad=:localidad, "
                            "provincia=:provincia, ccaa=:ccaa, "
                            "rating=:rating, num_resenas=:num_resenas, "
                            "score=:score, apto_campanya=:apto_campanya, "
                            "segmento=:segmento, "
                            f"last_seen_at='{now}', "
                            f"last_seen_job_id='{job_id}', "
                            "times_seen = times_seen + 1 "
                            "WHERE place_id=:place_id",
                            lead_data,
                        )
                    actualizados += 1

                # Relación N:N (idempotente)
                conn.execute(
                    "INSERT OR IGNORE INTO lead_jobs (place_id, job_id, seen_at) "
                    "VALUES (?, ?, ?)",
                    (place_id, job_id, now),
                )
    finally:
        conn.close()

    return {"nuevos": nuevos, "actualizados": actualizados, "total": total}


def _to_float(s) -> float | None:
    try:
        return float(s) if s not in (None, "", "None") else None
    except (ValueError, TypeError):
        return None


def _to_int(s) -> int | None:
    try:
        return int(s) if s not in (None, "", "None") else None
    except (ValueError, TypeError):
        return None


# -----------------------------------------------------------------------------
# Consultas
# -----------------------------------------------------------------------------

def list_leads(
    segmento: str = "",
    estado: str = "",
    email_status: str = "",
    apto_only: bool = False,
    search: str = "",
    order_by: str = "score",
    limit: int = 200,
    offset: int = 0,
) -> tuple[list, int]:
    """
    Devuelve (lista de leads, total sin paginar) aplicando filtros.

    `order_by` permitido: score, last_seen, first_seen, nombre.
    """
    where = []
    params = []

    if segmento:
        where.append("segmento = ?")
        params.append(segmento)
    if estado:
        if estado not in ESTADOS_VALIDOS:
            estado = ""
        else:
            where.append("estado = ?")
            params.append(estado)
    if email_status:
        if email_status in EMAIL_STATUS_VALIDOS:
            where.append("email_status = ?")
            params.append(email_status)
    if apto_only:
        where.append("apto_campanya = 1")
    if search:
        where.append("(nombre LIKE ? OR email LIKE ? OR localidad LIKE ?)")
        like = f"%{search}%"
        params.extend([like, like, like])

    where_sql = "WHERE " + " AND ".join(where) if where else ""

    order_map = {
        "score": "score DESC, last_seen_at DESC",
        "last_seen": "last_seen_at DESC",
        "first_seen": "first_seen_at DESC",
        "nombre": "nombre ASC",
    }
    order_sql = order_map.get(order_by, order_map["score"])

    db = get_db()
    total = db.execute(
        f"SELECT COUNT(*) AS n FROM leads_master {where_sql}",
        params,
    ).fetchone()["n"]

    rows = db.execute(
        f"SELECT * FROM leads_master {where_sql} "
        f"ORDER BY {order_sql} LIMIT ? OFFSET ?",
        (*params, limit, offset),
    ).fetchall()

    return list(rows), total


def get_lead(place_id: str):
    return get_db().execute(
        "SELECT * FROM leads_master WHERE place_id = ?", (place_id,)
    ).fetchone()


def lead_history(place_id: str) -> list:
    """Lista de jobs donde apareció este lead, más reciente primero."""
    return get_db().execute(
        "SELECT lj.job_id, lj.seen_at, j.segmento, j.ambito, j.queued_at, "
        "u.username "
        "FROM lead_jobs lj "
        "LEFT JOIN jobs j ON j.id = lj.job_id "
        "LEFT JOIN users u ON u.id = j.user_id "
        "WHERE lj.place_id = ? "
        "ORDER BY lj.seen_at DESC",
        (place_id,),
    ).fetchall()


def stats_overview() -> dict:
    """Resumen global de leads para el dashboard."""
    db = get_db()
    row = db.execute("""
        SELECT
          COUNT(*) AS total,
          SUM(CASE WHEN estado='nuevo' THEN 1 ELSE 0 END) AS nuevos,
          SUM(CASE WHEN estado='contactado' THEN 1 ELSE 0 END) AS contactados,
          SUM(CASE WHEN estado='respondio' THEN 1 ELSE 0 END) AS respondio,
          SUM(CASE WHEN estado='descartado' THEN 1 ELSE 0 END) AS descartados,
          SUM(CASE WHEN apto_campanya=1 THEN 1 ELSE 0 END) AS aptos,
          SUM(CASE WHEN email_status='mx_ok' THEN 1 ELSE 0 END) AS email_mx_ok,
          SUM(CASE WHEN email_status='mx_fail' THEN 1 ELSE 0 END) AS email_mx_fail
        FROM leads_master
    """).fetchone()
    return dict(row) if row else {}


# -----------------------------------------------------------------------------
# Mutaciones
# -----------------------------------------------------------------------------

def update_lead_state(place_id: str, estado: str, notas: str | None = None) -> bool:
    if estado not in ESTADOS_VALIDOS:
        return False
    db = get_db()
    now = datetime.now().isoformat(timespec="seconds")
    if estado == "contactado":
        # Solo actualizar fecha_ultimo_contacto al marcar como contactado
        if notas is not None:
            db.execute(
                "UPDATE leads_master SET estado=?, notas=?, fecha_ultimo_contacto=? "
                "WHERE place_id=?",
                (estado, notas[:5000], now, place_id),
            )
        else:
            db.execute(
                "UPDATE leads_master SET estado=?, fecha_ultimo_contacto=? "
                "WHERE place_id=?",
                (estado, now, place_id),
            )
    else:
        if notas is not None:
            db.execute(
                "UPDATE leads_master SET estado=?, notas=? WHERE place_id=?",
                (estado, notas[:5000], place_id),
            )
        else:
            db.execute(
                "UPDATE leads_master SET estado=? WHERE place_id=?",
                (estado, place_id),
            )
    return True


def update_lead_notes(place_id: str, notas: str) -> bool:
    get_db().execute(
        "UPDATE leads_master SET notas=? WHERE place_id=?",
        (notas[:5000], place_id),
    )
    return True


def validate_lead_email(place_id: str, method: str = "mx") -> dict | None:
    """
    Valida el email del lead via el método indicado y actualiza la BD.

    method:
      'mx'       → solo DNS (gratis, rápido)
      'smtp'     → SMTP handshake (gratis, arriesgado)
      'mailgun'  → Mailgun Email Validation API (de pago, fiable)

    Devuelve el dict con el resultado o None si el lead no existe o sin email.
    """
    row = get_db().execute(
        "SELECT email FROM leads_master WHERE place_id=?", (place_id,)
    ).fetchone()
    if row is None or not row["email"]:
        return None

    from .settings import load_settings
    from .email_validation import check_email_mx, check_email_smtp, check_email_mailgun

    if method == "smtp":
        result = check_email_smtp(row["email"])
    elif method == "mailgun":
        settings = load_settings()
        mailgun = settings.get("mailgun") or {}
        api_key = mailgun.get("api_key", "")
        base_url = mailgun.get("base_url", "https://api.mailgun.net")
        if not api_key:
            return {
                "email": row["email"],
                "status": "unknown",
                "reason": "Mailgun no configurado",
                "checked_at": datetime.now().isoformat(timespec="seconds"),
                "method": "mailgun",
            }
        result = check_email_mailgun(row["email"], api_key, base_url)
    else:
        result = check_email_mx(row["email"])

    get_db().execute(
        "UPDATE leads_master SET email_status=?, email_checked_at=?, "
        "email_check_method=? WHERE place_id=?",
        (result["status"], result["checked_at"],
         result.get("method", method), place_id),
    )
    return result


def validate_emails_batch(place_ids: list[str], method: str = "mx") -> dict:
    """Valida varios emails. Devuelve estadísticas."""
    ok = fail = skipped = 0
    for pid in place_ids:
        r = validate_lead_email(pid, method=method)
        if r is None:
            skipped += 1
        elif r["status"] in ("mx_ok", "verified"):
            ok += 1
        else:
            fail += 1
    return {"ok": ok, "fail": fail, "skipped": skipped, "total": len(place_ids)}


# -----------------------------------------------------------------------------
# Extracción de redes sociales
# -----------------------------------------------------------------------------

def extract_social_for_lead(place_id: str) -> dict | None:
    """
    Descarga la web del lead y extrae sus URLs de redes sociales.
    Actualiza la BD. Devuelve dict con las URLs encontradas, o None si
    el lead no existe o no tiene web.
    """
    from .social_extraction import extract_social_from_url

    row = get_db().execute(
        "SELECT web FROM leads_master WHERE place_id=?", (place_id,)
    ).fetchone()
    if row is None or not row["web"]:
        return None

    socials = extract_social_from_url(row["web"])
    now = datetime.now().isoformat(timespec="seconds")
    get_db().execute(
        "UPDATE leads_master SET "
        "linkedin_url=?, instagram_url=?, facebook_url=?, "
        "twitter_url=?, youtube_url=?, tiktok_url=?, "
        "social_extracted_at=? "
        "WHERE place_id=?",
        (
            socials["linkedin_url"], socials["instagram_url"],
            socials["facebook_url"], socials["twitter_url"],
            socials["youtube_url"], socials["tiktok_url"],
            now, place_id,
        ),
    )
    return socials


def extract_social_batch(place_ids: list[str]) -> dict:
    """Extrae redes sociales para varios leads. Devuelve estadísticas."""
    found_any = none_found = skipped = 0
    for pid in place_ids:
        r = extract_social_for_lead(pid)
        if r is None:
            skipped += 1
        elif any(r.values()):
            found_any += 1
        else:
            none_found += 1
    return {
        "found_any": found_any,
        "none_found": none_found,
        "skipped": skipped,
        "total": len(place_ids),
    }


# -----------------------------------------------------------------------------
# Export CSV
# -----------------------------------------------------------------------------

EXPORT_COLUMNS = [
    "place_id", "nombre", "email", "email_status", "email_check_method",
    "telefono", "web",
    "linkedin_url", "instagram_url", "facebook_url",
    "twitter_url", "youtube_url", "tiktok_url",
    "direccion", "localidad", "provincia", "ccaa",
    "rating", "num_resenas", "score", "apto_campanya",
    "segmento", "estado", "fecha_ultimo_contacto", "notas",
    "first_seen_at", "last_seen_at", "times_seen",
]


def export_leads_csv(rows) -> str:
    """Genera el CSV en memoria de la lista de leads dada."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=EXPORT_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow({k: r[k] if k in r.keys() else "" for k in EXPORT_COLUMNS})
    return buf.getvalue()


# -----------------------------------------------------------------------------
# Rutas
# -----------------------------------------------------------------------------

@bp.route("/")
@login_required
def index():
    """Listado de leads con filtros."""
    segmento = request.args.get("segmento", "").strip()
    estado = request.args.get("estado", "").strip()
    email_status = request.args.get("email_status", "").strip()
    apto_only = request.args.get("apto", "") == "1"
    search = (request.args.get("q", "") or "").strip()[:200]
    order_by = request.args.get("order", "score")

    try:
        page = max(1, int(request.args.get("page", "1")))
    except (ValueError, TypeError):
        page = 1
    per_page = 50
    offset = (page - 1) * per_page

    rows, total = list_leads(
        segmento=segmento, estado=estado, email_status=email_status,
        apto_only=apto_only, search=search, order_by=order_by,
        limit=per_page, offset=offset,
    )

    # Para los filtros (dropdowns)
    db = get_db()
    segmentos_distinct = [r["segmento"] for r in db.execute(
        "SELECT DISTINCT segmento FROM leads_master ORDER BY segmento"
    ).fetchall()]
    stats = stats_overview()

    total_pages = max(1, (total + per_page - 1) // per_page)

    return render_template(
        "leads_list.html",
        leads=rows, total=total, page=page, total_pages=total_pages, per_page=per_page,
        filtros={
            "segmento": segmento, "estado": estado,
            "email_status": email_status, "apto_only": apto_only,
            "search": search, "order_by": order_by,
        },
        segmentos_distinct=segmentos_distinct,
        stats=stats,
    )


@bp.route("/<place_id>")
@login_required
def detail(place_id):
    lead = get_lead(place_id)
    if lead is None:
        abort(404)
    history = lead_history(place_id)
    return render_template("lead_detail.html", lead=lead, history=history)


@bp.route("/<place_id>/state", methods=["POST"])
@login_required
def update_state(place_id):
    """Actualiza estado y/o notas del lead."""
    lead = get_lead(place_id)
    if lead is None:
        abort(404)
    estado = request.form.get("estado", "").strip()
    notas = request.form.get("notas")
    if estado and estado not in ESTADOS_VALIDOS:
        flash("Estado inválido.", "error")
        return redirect(url_for("leads.detail", place_id=place_id))
    if estado:
        update_lead_state(place_id, estado, notas)
        flash(f"Lead marcado como '{estado}'.", "success")
    elif notas is not None:
        update_lead_notes(place_id, notas)
        flash("Notas actualizadas.", "success")
    return redirect(url_for("leads.detail", place_id=place_id))


@bp.route("/<place_id>/validate-email", methods=["POST"])
@login_required
def validate_email_route(place_id):
    method = request.form.get("method", "mx")
    if method not in ("mx", "smtp", "mailgun"):
        method = "mx"
    result = validate_lead_email(place_id, method=method)
    if result is None:
        flash("El lead no tiene email o no existe.", "error")
    elif result["status"] in ("mx_ok", "verified"):
        flash(f"Email validado ({result.get('method', method)}): {result['reason']}.",
              "success")
    else:
        flash(f"Email no validable ({result.get('method', method)}): {result['reason']}.",
              "error")
    return redirect(url_for("leads.detail", place_id=place_id))


@bp.route("/<place_id>/extract-social", methods=["POST"])
@login_required
def extract_social_route(place_id):
    """Re-extrae redes sociales descargando la web del lead."""
    result = extract_social_for_lead(place_id)
    if result is None:
        flash("El lead no tiene web o no existe.", "error")
    else:
        n_found = sum(1 for v in result.values() if v)
        if n_found:
            flash(f"Redes extraídas: {n_found} encontradas.", "success")
        else:
            flash("No se han encontrado URLs de redes en la web.", "error")
    return redirect(url_for("leads.detail", place_id=place_id))


@bp.route("/bulk/validate-emails", methods=["POST"])
@login_required
def bulk_validate_emails():
    """
    Valida en lote los emails de los leads filtrados (los mismos que ve
    el usuario en la vista actual). El POST trae los mismos query args.
    """
    # Aceptamos hasta 500 a la vez para no bloquear demasiado el thread
    segmento = request.form.get("segmento", "").strip()
    estado = request.form.get("estado", "").strip()
    email_status = request.form.get("email_status", "").strip()
    apto_only = request.form.get("apto", "") == "1"
    search = (request.form.get("q", "") or "").strip()[:200]
    only_unvalidated = request.form.get("only_unvalidated") == "1"

    rows, _ = list_leads(
        segmento=segmento, estado=estado,
        email_status=email_status if not only_unvalidated else "",
        apto_only=apto_only, search=search,
        limit=500, offset=0,
    )

    # Filtrar los que tengan email no vacío
    place_ids = [r["place_id"] for r in rows if r["email"]]
    if only_unvalidated:
        place_ids = [
            r["place_id"] for r in rows
            if r["email"] and not r["email_status"]
        ]

    if not place_ids:
        flash("No hay emails sin validar en el filtro actual.", "error")
    else:
        method = request.form.get("method", "mx")
        if method not in ("mx", "smtp", "mailgun"):
            method = "mx"
        stats = validate_emails_batch(place_ids, method=method)
        flash(
            f"Validados {stats['total']} via {method}: "
            f"{stats['ok']} OK, {stats['fail']} inválidos.",
            "success",
        )

    # Volver al listado preservando los filtros
    return redirect(url_for("leads.index", **{
        k: v for k, v in request.form.items()
        if k not in ("csrf_token", "only_unvalidated")
    }))


@bp.route("/bulk/extract-social", methods=["POST"])
@login_required
def bulk_extract_social():
    """
    Extrae redes sociales en lote para los leads del filtro actual.
    Solo procesa leads que aún no tengan social_extracted_at.
    """
    segmento = request.form.get("segmento", "").strip()
    estado = request.form.get("estado", "").strip()
    apto_only = request.form.get("apto", "") == "1"
    search = (request.form.get("q", "") or "").strip()[:200]

    rows, _ = list_leads(
        segmento=segmento, estado=estado, apto_only=apto_only,
        search=search, limit=200, offset=0,
    )

    # Filtrar los que tengan web y no hayan sido extraídos aún
    place_ids = [
        r["place_id"] for r in rows
        if r["web"] and not r["social_extracted_at"]
    ]
    if not place_ids:
        flash("No hay leads pendientes de extracción de redes en el filtro actual.",
              "error")
    else:
        stats = extract_social_batch(place_ids)
        flash(
            f"Procesados {stats['total']} leads: "
            f"{stats['found_any']} con redes, "
            f"{stats['none_found']} sin redes encontradas.",
            "success",
        )

    return redirect(url_for("leads.index", **{
        k: v for k, v in request.form.items()
        if k != "csrf_token"
    }))


@bp.route("/export.csv")
@login_required
def export_csv():
    """
    Exporta los leads del filtro actual a CSV.
    Soporta `exclude_contacted=1` para no incluir los ya contactados/respondidos.
    """
    segmento = request.args.get("segmento", "").strip()
    estado = request.args.get("estado", "").strip()
    email_status = request.args.get("email_status", "").strip()
    apto_only = request.args.get("apto", "") == "1"
    search = (request.args.get("q", "") or "").strip()[:200]
    exclude_contacted = request.args.get("exclude_contacted") == "1"
    only_mx_ok = request.args.get("only_mx_ok") == "1"

    rows, _ = list_leads(
        segmento=segmento, estado=estado, email_status=email_status,
        apto_only=apto_only, search=search,
        limit=50000, offset=0,
    )

    if exclude_contacted:
        rows = [r for r in rows if r["estado"] not in ("contactado", "respondio")]
    if only_mx_ok:
        rows = [r for r in rows if r["email_status"] == "mx_ok"]

    csv_text = export_leads_csv(rows)
    fname = f"leads_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(
        csv_text,
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )
