"""
Blueprint de Mailgun:

  - GET  /mailgun/push       → formulario para empujar leads a una lista
  - POST /mailgun/push       → ejecuta el push
  - POST /webhooks/mailgun   → endpoint receptor de eventos (sin login,
                                pero verificado por firma HMAC)
"""

import json
from datetime import datetime

from flask import (
    Blueprint, abort, current_app, flash, jsonify, redirect, render_template,
    request, url_for,
)

from .auth import current_user, login_required
from .db import get_db, new_connection
from .leads import list_leads
from .mailgun import (
    is_configured, list_mailing_lists, push_members, verify_webhook_signature,
)
from .security import csrf


bp = Blueprint("mailgun", __name__)


# Mapeo de eventos de Mailgun → estado de lead.
# El webhook puede traer muchos tipos; estos son los que nos interesan.
EVENT_TO_STATE = {
    "delivered": "contactado",
    "opened": "contactado",          # idempotente: si ya estaba, sigue
    "clicked": "contactado",
    "complained": "descartado",
    "unsubscribed": "descartado",
    "failed": None,                  # mantenemos estado, pero marcamos en notas
    "rejected": None,
}


# -----------------------------------------------------------------------------
# Push de leads a mailing list
# -----------------------------------------------------------------------------

@bp.route("/mailgun/push", methods=["GET", "POST"])
@login_required
def push():
    """
    GET: formulario con dropdown de listas + filtros.
    POST: ejecuta el push.
    """
    if not is_configured():
        flash("Mailgun no está configurado. Avisa al admin.", "error")
        return redirect(url_for("dashboard.index"))

    # Listas disponibles
    lists, err = list_mailing_lists()
    if err:
        flash(f"No se pudieron cargar las listas de Mailgun: {err}", "error")
        lists = []

    if request.method == "GET":
        # Pasamos los filtros vigentes (si vienen via querystring desde /leads)
        filtros = {
            "segmento": request.args.get("segmento", ""),
            "estado": request.args.get("estado", ""),
            "email_status": request.args.get("email_status", "mx_ok"),
            "apto_only": request.args.get("apto", "1") == "1",
            "exclude_contacted": True,  # default razonable
            "search": request.args.get("q", ""),
        }
        # Pre-conteo del filtro actual
        rows, total = _filter_leads(filtros, limit=1)
        return render_template(
            "mailgun_push.html",
            lists=lists, filtros=filtros, total_preview=total,
        )

    # POST: ejecutar
    list_address = (request.form.get("list_address") or "").strip()
    if not list_address:
        flash("Selecciona o crea una lista de Mailgun.", "error")
        return redirect(url_for("mailgun.push"))

    filtros = {
        "segmento": request.form.get("segmento", ""),
        "estado": request.form.get("estado", ""),
        "email_status": request.form.get("email_status", "mx_ok"),
        "apto_only": request.form.get("apto", "0") == "1",
        "exclude_contacted": request.form.get("exclude_contacted", "0") == "1",
        "search": request.form.get("q", ""),
    }

    rows, total = _filter_leads(filtros, limit=5000)
    if not rows:
        flash("Ningún lead pasa los filtros. Push cancelado.", "error")
        return redirect(url_for("mailgun.push"))

    # Construir miembros
    members = []
    for r in rows:
        if not r["email"]:
            continue
        members.append({
            "address": r["email"],
            "name": r["nombre"] or r["email"],
            "subscribed": True,
            "upsert": True,
            "vars": {
                "place_id": r["place_id"],
                "segmento": r["segmento"],
                "localidad": r["localidad"] or "",
                "score": r["score"],
            },
        })

    if not members:
        flash("Los leads filtrados no tienen email. Push cancelado.", "error")
        return redirect(url_for("mailgun.push"))

    pushed, err = push_members(list_address, members)
    if err:
        flash(f"Push parcial: {pushed} leads enviados, error: {err}", "error")
    else:
        flash(
            f"Push completado: {pushed} leads enviados a {list_address}.",
            "success",
        )
        # Marcar los leads que se acaban de empujar
        # (no asumimos contactado todavía — eso lo hace el webhook al delivered)

    return redirect(url_for("mailgun.push"))


def _filter_leads(filtros: dict, limit: int = 5000):
    """Aplica los filtros y excluye contactados si corresponde."""
    rows, total = list_leads(
        segmento=filtros.get("segmento", ""),
        estado=filtros.get("estado", ""),
        email_status=filtros.get("email_status", ""),
        apto_only=filtros.get("apto_only", False),
        search=filtros.get("search", ""),
        limit=limit, offset=0,
    )
    if filtros.get("exclude_contacted"):
        rows = [r for r in rows if r["estado"] not in ("contactado", "respondio")]
        total = len(rows)
    # Solo los que tienen email
    rows = [r for r in rows if r["email"]]
    if filtros.get("exclude_contacted"):
        total = len(rows)
    return rows, total


# -----------------------------------------------------------------------------
# Webhook receiver
# -----------------------------------------------------------------------------

@bp.route("/webhooks/mailgun", methods=["POST"])
@csrf.exempt   # Mailgun no envía nuestro token CSRF, obviamente
def webhook():
    """
    Recibe eventos de Mailgun y actualiza estados de leads.

    Mailgun envía dos formatos:
      1. Legacy (form-encoded): campos planos timestamp, token, signature, ...
      2. Nuevo (JSON): {"signature": {...}, "event-data": {...}}

    Soportamos ambos. Verificamos firma HMAC SIEMPRE antes de procesar.
    """
    # Detectar formato
    payload = None
    timestamp = token = signature = ""
    event = recipient = ""

    if request.content_type and request.content_type.startswith("application/json"):
        try:
            payload = request.get_json(force=True)
        except Exception:
            return jsonify({"error": "JSON inválido"}), 400
        if not isinstance(payload, dict):
            return jsonify({"error": "Payload no es objeto"}), 400
        sig_block = payload.get("signature") or {}
        ev = payload.get("event-data") or {}
        timestamp = str(sig_block.get("timestamp", ""))
        token = sig_block.get("token", "")
        signature = sig_block.get("signature", "")
        event = ev.get("event", "")
        recipient = ev.get("recipient", "")
    else:
        # Form-encoded
        timestamp = request.form.get("timestamp", "")
        token = request.form.get("token", "")
        signature = request.form.get("signature", "")
        event = request.form.get("event", "")
        recipient = request.form.get("recipient", "")

    # Verificar firma
    if not verify_webhook_signature(timestamp, token, signature):
        current_app.logger.warning(
            "Webhook Mailgun rechazado: firma inválida (event=%s, recipient=%s)",
            event, recipient,
        )
        return jsonify({"error": "firma inválida"}), 401

    # Procesar evento
    new_state = EVENT_TO_STATE.get(event)
    note_suffix = f"\n[mailgun {event} @ {datetime.now().isoformat(timespec='seconds')}]"

    conn = new_connection()
    try:
        # Por email puede haber varios leads (raro pero posible). Actualizar todos.
        lead_rows = conn.execute(
            "SELECT place_id, estado, notas FROM leads_master WHERE email = ?",
            (recipient.lower().strip(),),
        ).fetchall()
        if not lead_rows:
            current_app.logger.info(
                "Webhook Mailgun: no encontrado lead con email=%s (event=%s)",
                recipient, event,
            )
            return jsonify({"status": "ok", "updated": 0}), 200

        updated = 0
        for lead in lead_rows:
            new_notas = (lead["notas"] or "") + note_suffix
            if new_state is None:
                # Solo añadir nota
                conn.execute(
                    "UPDATE leads_master SET notas=? WHERE place_id=?",
                    (new_notas[:5000], lead["place_id"]),
                )
            else:
                # Solo escalamos a 'contactado' si estaba 'nuevo';
                # un 'respondio' no se baja a 'contactado'.
                if new_state == "contactado" and lead["estado"] != "nuevo":
                    conn.execute(
                        "UPDATE leads_master SET notas=? WHERE place_id=?",
                        (new_notas[:5000], lead["place_id"]),
                    )
                else:
                    now = datetime.now().isoformat(timespec="seconds")
                    if new_state == "contactado":
                        conn.execute(
                            "UPDATE leads_master SET estado=?, notas=?, "
                            "fecha_ultimo_contacto=? WHERE place_id=?",
                            (new_state, new_notas[:5000], now, lead["place_id"]),
                        )
                    else:
                        conn.execute(
                            "UPDATE leads_master SET estado=?, notas=? "
                            "WHERE place_id=?",
                            (new_state, new_notas[:5000], lead["place_id"]),
                        )
            updated += 1
    finally:
        conn.close()

    return jsonify({"status": "ok", "updated": updated, "event": event}), 200
