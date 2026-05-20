#!/usr/bin/env python3
"""
PPM_run_script.py — wrapper que carga segmentos personalizados desde
PPM_EXTRA_SEGMENTS (JSON) y luego ejecuta uno de los scripts del proyecto.

Por qué existe:
  Los scripts del proyecto (`scripts/buscar.py`, etc.) leen los segmentos
  desde `config.segmentos.SEGMENTOS`, que es estático. La UI necesita poder
  lanzar segmentos creados por el usuario sin modificar ese fichero. Este
  wrapper inyecta los segmentos en `SEGMENTOS` en memoria antes de delegar
  en el script real, así no tocamos el código fuente original.

Uso:
  PPM_EXTRA_SEGMENTS=/ruta/extra.json \
  python -m webui.run_with_extras scripts/buscar.py --segmento mi_seg --ambito espana

El JSON apuntado por PPM_EXTRA_SEGMENTS debe tener la forma:
  {
    "mi_segmento": {
      "nombre_humano": "...",
      "producto_cgd": "...",
      "queries": ["..."],
      "palabras_clave_web": ["..."],
      "palabras_descarte": ["..."]
    }
  }
"""

import json
import os
import runpy
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def inject_extra_segments():
    extra_path = os.environ.get("PPM_EXTRA_SEGMENTS", "")
    if not extra_path:
        return 0
    p = Path(extra_path)
    if not p.is_file():
        return 0
    try:
        with open(p, encoding="utf-8") as f:
            extras = json.load(f)
    except (json.JSONDecodeError, OSError):
        return 0

    # Importar SEGMENTOS y SCORING_BASE
    from config import segmentos as seg_mod

    for sid, sdata in extras.items():
        if sid in seg_mod.SEGMENTOS:
            # No pisar segmentos del sistema
            continue
        seg_mod.SEGMENTOS[sid] = {
            "nombre_humano": sdata.get("nombre_humano", sid),
            "producto_cgd": sdata.get("producto_cgd", ""),
            "queries": sdata.get("queries", []),
            "palabras_clave_web": sdata.get("palabras_clave_web", []),
            "palabras_descarte": sdata.get("palabras_descarte", []),
            "reglas_scoring": seg_mod.SCORING_BASE,
        }
    return len(extras)


def main():
    if len(sys.argv) < 2:
        print("Uso: python -m webui.run_with_extras <script.py> [args...]",
              file=sys.stderr)
        return 2
    script = sys.argv[1]
    # Re-escribir sys.argv para que el script crea que se invoca normalmente
    sys.argv = sys.argv[1:]

    count = inject_extra_segments()
    if count:
        print(f"[run_with_extras] Inyectados {count} segmento(s) personalizado(s).")

    script_path = Path(script)
    if not script_path.is_absolute():
        script_path = PROJECT_ROOT / script_path

    if not script_path.is_file():
        print(f"ERROR: no existe el script {script_path}", file=sys.stderr)
        return 1

    # Ejecutar el script como __main__
    runpy.run_path(str(script_path), run_name="__main__")
    return 0


if __name__ == "__main__":
    sys.exit(main())
