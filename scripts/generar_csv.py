#!/usr/bin/env python3
"""
CLI — Genera el CSV final aplicando scoring del segmento.

Uso:
    python scripts/generar_csv.py --input enriquecido_<segmento>_<ambito>_<fecha>.json
"""

import argparse
import csv
import json
import logging
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.base import SCORE_MINIMO_CAMPANYA, DATA_DIR, LOG_DIR
from core.modelos import Negocio
from core.scoring import calcular_score


COLUMNAS_CSV = [
    "score", "apto_campanya",
    "nombre", "email", "telefono", "web",
    "direccion", "codigo_postal", "localidad", "provincia", "ccaa",
    "rating", "num_resenas", "estado_negocio",
    "email_dominio_propio", "tiene_apartado_lopivi",
    "palabras_clave_encontradas",
    "segmento", "place_id",
    "queries_origen", "ciudades_origen",
    "fecha_extraccion", "motivos_score",
]


def es_lead_valido(n: Negocio) -> bool:
    if n.estado_negocio == "CLOSED_PERMANENTLY":
        return False
    if n.palabras_descarte_encontradas:
        return False
    if not (n.email or n.web or n.telefono):
        return False
    return True


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True, help="JSON de extraer_emails.py")
    return p.parse_args()


def resolver_input(arg: str) -> Path:
    pth = Path(arg)
    return pth if pth.is_file() else DATA_DIR / arg


def main() -> int:
    args = parse_args()
    ruta_in = resolver_input(args.input)
    if not ruta_in.is_file():
        print(f"ERROR: no se encuentra {args.input}", file=sys.stderr)
        return 1

    log_file = LOG_DIR / f"csv_{ruta_in.stem}_{datetime.now():%Y%m%d_%H%M%S}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler(log_file, encoding="utf-8"),
                  logging.StreamHandler(sys.stdout)],
    )
    log = logging.getLogger("csv")

    log.info("=" * 72)
    log.info("CGD - Generación de CSV final")
    log.info("Entrada: %s", ruta_in)
    log.info("=" * 72)

    with open(ruta_in, encoding="utf-8") as f:
        raw = json.load(f)
    negocios = [Negocio(**d) for d in raw]
    log.info("Cargados: %d negocios", len(negocios))

    # Scoring
    for n in negocios:
        n.score, n.motivos_score = calcular_score(n)

    # Filtro
    validos = [n for n in negocios if es_lead_valido(n)]
    descartados = len(negocios) - len(validos)
    log.info("Válidos: %d  |  Descartados: %d", len(validos), descartados)

    aptos = [n for n in validos if n.score >= SCORE_MINIMO_CAMPANYA]
    log.info("Aptos para campaña (score >= %d): %d (%.1f%%)",
             SCORE_MINIMO_CAMPANYA, len(aptos),
             100*len(aptos)/max(len(validos), 1))

    log.info("")
    log.info("=== Distribución de score ===")
    for umbral in (90, 80, 70, 60, 50, 40, 30, 20):
        n = sum(1 for x in validos if x.score >= umbral)
        log.info("  >= %d: %4d", umbral, n)

    log.info("")
    log.info("=== Aptos por CCAA ===")
    por_ccaa = {}
    for n in aptos:
        clave = n.ccaa or "(sin)"
        por_ccaa[clave] = por_ccaa.get(clave, 0) + 1
    for ccaa, n in sorted(por_ccaa.items(), key=lambda x: -x[1])[:15]:
        log.info("  %-30s %4d", ccaa, n)

    # CSV
    salida = DATA_DIR / f"leads_{ruta_in.stem.replace('enriquecido_', '')}.csv"
    validos.sort(key=lambda x: -x.score)
    with open(salida, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNAS_CSV, extrasaction="ignore")
        writer.writeheader()
        for n in validos:
            row = asdict(n)
            row["apto_campanya"] = "SI" if n.score >= SCORE_MINIMO_CAMPANYA else "NO"
            row["palabras_clave_encontradas"] = " | ".join(n.palabras_clave_encontradas)
            row["queries_origen"] = " | ".join(n.queries_origen)
            row["ciudades_origen"] = " | ".join(n.ciudades_origen)
            row["motivos_score"] = " | ".join(n.motivos_score)
            writer.writerow(row)

    log.info("")
    log.info("=" * 72)
    log.info("✅ CSV: %s", salida)
    log.info("   Filas: %d  |  Aptos: %d", len(validos), len(aptos))
    log.info("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
