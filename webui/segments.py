"""
Gestión de segmentos.

Los 8 segmentos del sistema se importan de config/segmentos.py (no se tocan).
Los segmentos personalizados se guardan en la tabla `segments` de SQLite,
**no** en JSON. Esto resuelve la race condition que sí teníamos en la primera
versión cuando dos creaciones concurrentes pisaban el archivo.
"""

import json
import re
import sys
from datetime import datetime

from flask import (
    Blueprint, flash, redirect, render_template, request, url_for,
)

from .auth import current_user, login_required
from .db import get_db
from .paths import PROJECT_ROOT


# Importar SEGMENTOS del proyecto, sin modificarlo
sys.path.insert(0, str(PROJECT_ROOT))
from config.segmentos import SEGMENTOS as BUILTIN_SEGMENTS  # noqa: E402


bp = Blueprint("segments", __name__, url_prefix="/segments")


SID_RE = re.compile(r"^[a-z][a-z0-9_]{1,49}$")


# -----------------------------------------------------------------------------
# Acceso a datos
# -----------------------------------------------------------------------------

def _row_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "nombre_humano": row["nombre_humano"],
        "producto_cgd": row["producto_cgd"],
        "queries": json.loads(row["queries_json"]),
        "palabras_clave_web": json.loads(row["palabras_clave_json"]),
        "palabras_descarte": json.loads(row["palabras_descarte_json"]),
        "builtin": False,
    }


def list_custom_segments() -> list[dict]:
    rows = get_db().execute(
        "SELECT * FROM segments ORDER BY id"
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_custom_segment(sid: str) -> dict | None:
    row = get_db().execute(
        "SELECT * FROM segments WHERE id = ?", (sid,)
    ).fetchone()
    return _row_to_dict(row) if row else None


def all_segments() -> dict:
    """Devuelve un dict {sid: info} con builtins + custom."""
    out = {}
    for sid, sdata in BUILTIN_SEGMENTS.items():
        out[sid] = {
            "id": sid,
            "nombre_humano": sdata["nombre_humano"],
            "producto_cgd": sdata["producto_cgd"],
            "queries": sdata["queries"],
            "palabras_clave_web": sdata.get("palabras_clave_web", []),
            "palabras_descarte": sdata.get("palabras_descarte", []),
            "builtin": True,
        }
    for s in list_custom_segments():
        out[s["id"]] = s
    return out


def create_custom_segment(
    sid: str, nombre: str, producto: str,
    queries: list, palabras: list, descarte: list,
    created_by: int | None,
) -> tuple[bool, list[str]]:
    """Crea un segmento custom. Devuelve (ok, errors)."""
    errors = _validate(sid, nombre, queries)
    if errors:
        return False, errors

    try:
        get_db().execute(
            "INSERT INTO segments "
            "(id, nombre_humano, producto_cgd, queries_json, "
            "palabras_clave_json, palabras_descarte_json, created_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                sid, nombre, producto,
                json.dumps(queries, ensure_ascii=False),
                json.dumps(palabras, ensure_ascii=False),
                json.dumps(descarte, ensure_ascii=False),
                created_by,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        return True, []
    except Exception as e:
        return False, [f"Error al guardar: {e}"]


def delete_custom_segment(sid: str) -> bool:
    cur = get_db().execute("DELETE FROM segments WHERE id = ?", (sid,))
    return cur.rowcount > 0


def _validate(sid: str, nombre: str, queries: list) -> list[str]:
    errors = []
    if not SID_RE.fullmatch(sid):
        errors.append(
            "ID inválido. Usa snake_case: solo letras minúsculas (a-z), "
            "números y guion bajo, empezando por letra (2-50 caracteres)."
        )
    if not nombre or len(nombre) > 200:
        errors.append("El nombre legible es obligatorio y debe tener ≤200 caracteres.")
    if not queries:
        errors.append("Debes añadir al menos una query de búsqueda.")
    if len(queries) > 100:
        errors.append("Demasiadas queries (máximo 100).")
    if any(len(q) > 200 for q in queries):
        errors.append("Alguna query supera los 200 caracteres.")
    if sid in BUILTIN_SEGMENTS:
        errors.append(f"'{sid}' ya existe como segmento del sistema.")
    # La unicidad contra customs la garantiza el PRIMARY KEY a nivel BD,
    # pero damos un error más legible si lo detectamos antes:
    existing = get_db().execute(
        "SELECT 1 FROM segments WHERE id = ?", (sid,)
    ).fetchone()
    if existing:
        errors.append(f"'{sid}' ya existe como segmento personalizado.")
    return errors


# -----------------------------------------------------------------------------
# Rutas
# -----------------------------------------------------------------------------

@bp.route("/")
@login_required
def index():
    return render_template("segments.html", segments=all_segments())


@bp.route("/new", methods=["GET", "POST"])
@login_required
def new():
    if request.method == "POST":
        sid = (request.form.get("id") or "").strip().lower()
        nombre = (request.form.get("nombre_humano") or "").strip()
        producto = (request.form.get("producto_cgd") or "").strip()
        queries = _parse_lines(request.form.get("queries", ""))
        palabras = _parse_lines(request.form.get("palabras_clave_web", ""))
        descarte = _parse_lines(request.form.get("palabras_descarte", ""))

        u = current_user()
        ok, errors = create_custom_segment(
            sid, nombre, producto, queries, palabras, descarte,
            created_by=u["id"] if u else None,
        )
        if not ok:
            for e in errors:
                flash(e, "error")
            return render_template("segment_form.html", form=request.form)
        flash(f"Segmento '{sid}' creado.", "success")
        return redirect(url_for("segments.index"))

    return render_template("segment_form.html", form={})


@bp.route("/<sid>/delete", methods=["POST"])
@login_required
def delete(sid):
    # Defensa en profundidad: aunque la BD no contenga el sid, validamos
    # el formato para no permitir caracteres raros en log/flash.
    if not SID_RE.fullmatch(sid):
        flash("ID inválido.", "error")
        return redirect(url_for("segments.index"))
    if sid in BUILTIN_SEGMENTS:
        flash("No se pueden borrar los segmentos del sistema.", "error")
        return redirect(url_for("segments.index"))
    if delete_custom_segment(sid):
        flash(f"Segmento '{sid}' eliminado.", "success")
    else:
        flash("No existe ese segmento personalizado.", "error")
    return redirect(url_for("segments.index"))


def _parse_lines(text: str) -> list[str]:
    return [line.strip() for line in (text or "").splitlines() if line.strip()]
