"""Dashboard principal: listado de jobs + lanzador de búsquedas."""

import sys

from flask import Blueprint, render_template

from .auth import current_user, login_required
from .db import get_db
from .jobs import monthly_spend
from .paths import PROJECT_ROOT
from .segments import all_segments
from .settings import get_api_key

sys.path.insert(0, str(PROJECT_ROOT))
from config.ciudades_espana import SUBCONJUNTOS  # noqa: E402


bp = Blueprint("dashboard", __name__)


@bp.route("/")
@login_required
def index():
    u = current_user()
    db = get_db()

    if u["role"] == "admin":
        jobs = db.execute(
            "SELECT j.*, us.username FROM jobs j "
            "JOIN users us ON us.id = j.user_id "
            "ORDER BY queued_at DESC LIMIT 50"
        ).fetchall()
    else:
        jobs = db.execute(
            "SELECT j.*, ? AS username FROM jobs j "
            "WHERE user_id = ? ORDER BY queued_at DESC LIMIT 50",
            (u["username"], u["id"]),
        ).fetchall()

    # Información de presupuesto para el usuario actual
    spent = monthly_spend(u["id"])
    budget = float(u["budget_eur_monthly"]) if u["budget_eur_monthly"] else 0

    # Tamaños de los ámbitos (para el cálculo de coste en cliente)
    ambitos_info = {
        a: {"nombre": a, "num_ciudades": len(cs)}
        for a, cs in SUBCONJUNTOS.items()
    }

    # Para los segmentos, contar queries y exponer al cliente
    segs = all_segments()
    segments_meta = {
        sid: {
            "nombre_humano": s["nombre_humano"],
            "num_queries": len(s["queries"]),
            "builtin": s["builtin"],
        }
        for sid, s in segs.items()
    }

    return render_template(
        "dashboard.html",
        jobs=jobs,
        segments=segs,
        segments_meta=segments_meta,
        ambitos=list(SUBCONJUNTOS.keys()),
        ambitos_info=ambitos_info,
        api_key_ok=bool(get_api_key()),
        monthly_spent=spent,
        monthly_budget=budget,
    )
