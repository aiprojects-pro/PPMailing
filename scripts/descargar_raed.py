#!/usr/bin/env python3
"""Descarga entidades del RAED a CSV."""

import argparse
import csv
import logging
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.base import DATA_DIR, LOG_DIR
from core.scraper_raed import (
    TIPOS_ENTIDAD,
    PROVINCIAS_RAED,
    crear_sesion,
    descargar_provincia_tipo,
)


def parse_args():
    p = argparse.ArgumentParser(description="Descargar entidades del RAED.")
    p.add_argument("--provincia", default="SEVILLA", choices=sorted(PROVINCIAS_RAED.keys()))
    p.add_argument("--tipo", default="Club deportivo", choices=sorted(TIPOS_ENTIDAD.keys()))
    p.add_argument("--rate-limit", type=float, default=1.5)
    p.add_argument("--max-paginas", type=int, default=None, help="Límite opcional para pruebas")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    log_file = LOG_DIR / f"raed_{args.provincia}_{datetime.now():%Y%m%d_%H%M%S}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler(log_file, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
    )

    sesion = crear_sesion()
    entidades = descargar_provincia_tipo(
        sesion=sesion,
        provincia_nombre=args.provincia,
        tipo_nombre=args.tipo,
        rate_limit_seg=args.rate_limit,
        max_paginas=args.max_paginas,
    )

    provincia_slug = args.provincia.lower().replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u")
    tipo_slug = args.tipo.lower().replace(" ", "_")
    salida = DATA_DIR / f"raed_{provincia_slug}_{tipo_slug}_{datetime.now():%Y%m%d}.csv"
    columnas = list(asdict(entidades[0]).keys()) if entidades else list(asdict(__import__("core.scraper_raed", fromlist=["EntidadRAED"]).EntidadRAED()).keys())
    with open(salida, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columnas)
        writer.writeheader()
        for entidad in entidades:
            writer.writerow(asdict(entidad))

    logging.info("CSV RAED: %s", salida)
    logging.info("Filas: %d", len(entidades))
    return 0


if __name__ == "__main__":
    sys.exit(main())
