#!/usr/bin/env python3
"""
CLI — Enriquece un CSV de negocios (nombre + localidad) con datos de Google Places.

Útil para:
  - Enriquecer el CSV del CGCAFE (23 colegios institucionales) con email/web.
  - Enriquecer salidas del scraper RAED cuando estén disponibles.
  - Enriquecer cualquier listado de Excel del equipo comercial.

Uso:
    python scripts/cruzar_csv.py \\
        --input data/colegios_cgcafe_inicial.csv \\
        --segmento admin_fincas \\
        --campo-nombre nombre \\
        --campo-localidad localidad \\
        --campo-provincia provincia

El CSV de entrada debe tener columnas con los nombres indicados.
La salida es un nuevo CSV con TODAS las columnas originales + las de Google.
"""

import argparse
import csv
import json
import logging
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.base import DATA_DIR, LOG_DIR
from config.segmentos import obtener_segmento
from core.cruce_places import cruzar_con_places, EntradaParaCruce


def parse_args():
    p = argparse.ArgumentParser(description="Enriquecer CSV con Google Places.")
    p.add_argument("--input", required=True, help="CSV de entrada")
    p.add_argument("--segmento", required=True,
                   help="ID del segmento (admin_fincas, clubes_deportivos, etc.)")
    p.add_argument("--campo-nombre", default="nombre",
                   help="Nombre de la columna con el nombre comercial (defecto: nombre)")
    p.add_argument("--campo-localidad", default="localidad",
                   help="Columna con la localidad (defecto: localidad)")
    p.add_argument("--campo-provincia", default="provincia",
                   help="Columna con la provincia (defecto: provincia)")
    p.add_argument("--campo-id", default="",
                   help="Columna con el ID externo, para trazabilidad (opcional)")
    p.add_argument("--fuente-origen", default="manual",
                   help="Etiqueta de origen para trazabilidad (defecto: manual)")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    ruta_in = Path(args.input)
    if not ruta_in.is_file():
        # Probar también dentro de data/
        ruta_in = DATA_DIR / args.input
    if not ruta_in.is_file():
        print(f"ERROR: archivo no encontrado: {args.input}", file=sys.stderr)
        return 1

    log_file = LOG_DIR / f"cruce_{ruta_in.stem}_{datetime.now():%Y%m%d_%H%M%S}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler(log_file, encoding="utf-8"),
                  logging.StreamHandler(sys.stdout)],
    )
    log = logging.getLogger("cruzar_csv")

    # Validar segmento
    try:
        seg = obtener_segmento(args.segmento)
    except ValueError as e:
        log.error(str(e))
        return 1

    log.info("=" * 72)
    log.info("CGD - Enriquecimiento de CSV con Google Places")
    log.info("Entrada:   %s", ruta_in)
    log.info("Segmento:  %s (%s)", args.segmento, seg["nombre_humano"])
    log.info("=" * 72)

    # Leer CSV
    entradas = []
    columnas_originales = []
    with open(ruta_in, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        columnas_originales = reader.fieldnames or []
        for row in reader:
            nombre = (row.get(args.campo_nombre) or "").strip()
            if not nombre:
                continue
            entradas.append(EntradaParaCruce(
                nombre=nombre,
                localidad=(row.get(args.campo_localidad) or "").strip(),
                provincia=(row.get(args.campo_provincia) or "").strip(),
                fuente_id_externo=(row.get(args.campo_id) or "").strip() if args.campo_id else "",
                datos_origen={k: v for k, v in row.items()
                              if v and k not in (args.campo_nombre,
                                                 args.campo_localidad,
                                                 args.campo_provincia,
                                                 args.campo_id)},
            ))

    log.info("Entradas a procesar: %d", len(entradas))
    if not entradas:
        log.error("No hay entradas. Verifica los nombres de columna con --campo-*")
        return 1

    # Ejecutar el cruce
    resultados = cruzar_con_places(
        entradas=entradas,
        segmento=args.segmento,
        fuente_origen=args.fuente_origen,
    )

    # Guardar CSV de salida
    salida = DATA_DIR / f"enriquecido_{ruta_in.stem}_{datetime.now():%Y%m%d}.csv"
    columnas_salida = [
        "nombre", "email", "telefono", "web", "rating", "num_resenas",
        "direccion", "codigo_postal", "localidad", "provincia", "ccaa",
        "estado_negocio", "place_id", "latitud", "longitud",
        "fuente", "fuente_id_externo", "segmento", "fecha_extraccion",
    ]
    with open(salida, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columnas_salida, extrasaction="ignore")
        writer.writeheader()
        for n in resultados:
            writer.writerow(asdict(n))

    enriquecidos = sum(1 for n in resultados if n.place_id)
    log.info("")
    log.info("=" * 72)
    log.info("✅ CSV enriquecido: %s", salida)
    log.info("   Total filas:       %d", len(resultados))
    log.info("   Enriquecidas:      %d (%.1f%%)",
             enriquecidos, 100*enriquecidos/max(len(resultados), 1))
    log.info("=" * 72)
    log.info("")
    log.info("Siguiente paso (opcional): aplicar extraer_emails.py")
    log.info("para sacar emails directamente de las webs encontradas.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
