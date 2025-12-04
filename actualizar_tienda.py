import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


from config_tienda import (
    PROJECT_ROOT,
    PROCESADAS_DIR,
    DEPLOY_DIR,
    INVENTORY_CSV,
    OUTPUT_HTML,
    DEPLOY_IMAGES_DIR,
    GIT_REPO_DIR,
)

# ========== CONFIGURACIÓN DE RUTAS ==========

# Carpeta donde están este script y el inventario
PROJECT_DIR = PROJECT_ROOT  # normalmente .../inventario_magic

# =========================
# Funciones auxiliares
# =========================

def safe_int(v, default=0):
    try:
        if v in ("", None):
            return default
        return int(v)
    except (ValueError, TypeError):
        return default


def safe_float(v):
    try:
        if v in ("", None):
            return None
        return float(v)
    except (ValueError, TypeError):
        return None


# ========== UTILIDADES GENERALES ==========

def run_cmd(cmd, cwd=None):
    """Ejecuta un comando de sistema mostrando salida y devolviendo returncode."""
    print(f"[CMD] {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(cwd) if cwd else None)
    return result.returncode


def run_script(script_name: str):
    """Ejecuta un script Python dentro de PROJECT_DIR."""
    print(f"\n[INFO] Ejecutando {script_name}...")
    result = subprocess.run(
        [sys.executable, script_name],
        cwd=str(PROJECT_DIR),
        text=True
    )
    if result.returncode != 0:
        raise SystemExit(f"[ERROR] El script {script_name} terminó con error (código {result.returncode}).")
    print(f"[OK] {script_name} ejecutado correctamente.")


def format_clp(value):
    """
    Formatea precios CLP. Acepta tanto int como string.
    Si viene vacío o None → devuelve 'Consultar'.
    """
    if value is None:
        return "Consultar"

    # Si es string, limpiarlo
    if isinstance(value, str):
        value = value.strip()
        if value == "":
            return "Consultar"
        try:
            value = int(value)
        except ValueError:
            return "Consultar"

    # Si llega aquí, value es int
    try:
        return f"${value:,.0f}".replace(",", ".")
    except:
        return "Consultar"




# ========== LECTURA DEL INVENTARIO ==========

def load_inventory(csv_path: Path) -> List[Dict]:
    """
    Carga inventario_cartas.csv y devuelve una lista de filas filtradas:
    - Solo status = 'available'
    - Solo quantity > 0
    Además calcula price_display (CLP formateado) y normaliza is_foil.
    """
    if not csv_path.exists():
        raise SystemExit(f"[ERROR] No se encontró el CSV de inventario: {csv_path}")

    rows: List[Dict] = []
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            status = (row.get("status") or "").strip().lower()
            if status not in {"available", "avail", ""}:
                continue

            try:
                quantity = int(row.get("quantity", 0))
            except ValueError:
                quantity = 0

            if quantity <= 0:
                continue

            row["quantity"] = quantity
            row["price_display"] = format_clp((row.get("price_clp") or "").strip())
            row["is_foil"] = (row.get("is_foil") or "").strip().lower()
            row["image_url"] = (row.get("image_url") or "").strip()
            rows.append(row)

    print(f"[INFO] Inventario cargado: {len(rows)} cartas disponibles.")
    return rows


# ========== PREPARACIÓN DE CARTAS PARA EL FRONT ==========

def prepare_cards_for_frontend(rows: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """
    Transforma las filas del CSV en una estructura optimizada para el frontend.

    NUEVA LÓGICA:

    - AGRUPA SOLO POR NOMBRE (name), ignorando set, foil, condición, formato, etc.
      Esto permite que distintas copias físicas de la misma carta se vean como
      un solo ítem en la grilla.

    - Dentro de cada grupo:
        - quantity = suma de quantity de todas las filas.
        - langs = conjunto de idiomas.
        - sets = conjunto de sets.
        - price:
            1) Primero se busca el precio_clp más BAJO entre copias NO FOIL.
            2) Si no hay copias no foil con precio, se toma el más BAJO entre todas.
            3) Si ninguna tiene precio_clp > 0 -> "Consultar".

        - copies = detalle por copia (para el modal), incluyendo:
            imageFile, quantity, lang, condition, format, isFoil, priceClp, set.

        - imageFile = imagen principal (la primera del grupo).
        - condition = condición "dominante" (mejor condición).
        - format = formato asociado a la condición dominante.
        - hasFoil / hasNonFoil = flags para saber si hay foil o no foil en el grupo.
    """

    # Orden de "mejor" condición para mostrar en la tarjeta
    condition_order = {
        "NM": 5,
        "EX": 4,
        "SP": 3,
        "MP": 2,
        "HP": 1,
    }

    def condition_rank(cond: str) -> int:
        return condition_order.get(cond.upper(), 0)

    groups: Dict[str, Dict[str, Any]] = {}

    for row in rows:
        name = (row.get("name") or "").strip()
        if not name:
            continue

        set_code = (row.get("set") or "").strip()
        condition = (row.get("condition") or "").strip().upper()
        fmt = (row.get("format") or "").strip()
        lang = (row.get("lang") or "").strip().upper()
        is_foil_flag = str(row.get("is_foil") or "").strip().lower() == "true"

        quantity = safe_int(row.get("quantity"), 0)
        if quantity <= 0:
            continue

        image_file = row.get("image_url") or ""
        price_clp_str = (row.get("price_clp") or "").strip()
        price_clp_val = safe_int(price_clp_str, 0)
        price_usd_ref = safe_float(row.get("price_usd_ref"))

        key = name.lower()  # 👈 AGRUPAMOS SOLO POR NOMBRE

        if key not in groups:
            groups[key] = {
                "name": name,
                "set_codes": set(),           # varios sets posibles
                "condition": condition,
                "format": fmt,
                "isFoil": False,              # se definirá después
                "hasFoil": False,
                "hasNonFoil": False,
                "quantity": 0,
                "langs": set(),
                "copies": [],
                "best_price_clp": None,       # int
                "best_price_usd_ref": None,   # float
                "imageFile": image_file,
                "condition_rank": condition_rank(condition),
                # para elegir mejor precio:
                "best_price_clp_nonfoil": None,
                "best_price_usd_nonfoil": None,
            }

        g = groups[key]

        # Sumar cantidad
        g["quantity"] += quantity

        # Idiomas y sets
        if lang:
            g["langs"].add(lang)
        if set_code:
            g["set_codes"].add(set_code)

        # Marcar foil / no foil
        if is_foil_flag:
            g["hasFoil"] = True
        else:
            g["hasNonFoil"] = True

        # Actualizar condición/format dominante si corresponde
        r_new = condition_rank(condition)
        if r_new > g.get("condition_rank", 0):
            g["condition"] = condition
            g["format"] = fmt
            g["condition_rank"] = r_new

        # Guardar detalle de copia para el modal
        g["copies"].append(
            {
                "imageFile": image_file,
                "quantity": quantity,
                "lang": lang,
                "condition": condition,
                "format": fmt,
                "isFoil": is_foil_flag,
                "priceClp": price_clp_val,
                "set": set_code,
            }
        )

        # Lógica de precios:
        # 1) Guardar mejor precio NO FOIL
        if not is_foil_flag and price_clp_val > 0:
            if g["best_price_clp_nonfoil"] is None or price_clp_val < g["best_price_clp_nonfoil"]:
                g["best_price_clp_nonfoil"] = price_clp_val
                g["best_price_usd_nonfoil"] = price_usd_ref

        # 2) Guardar mejor precio en general (por si todas son foil)
        if price_clp_val > 0:
            if g["best_price_clp"] is None or price_clp_val < g["best_price_clp"]:
                g["best_price_clp"] = price_clp_val
                g["best_price_usd_ref"] = price_usd_ref

    # Convertir grupos en lista para el frontend
    cards: List[Dict[str, Any]] = []
    for key, g in groups.items():
        langs_sorted = sorted(list(g["langs"])) if g["langs"] else []
        lang_display = "/".join(langs_sorted) if langs_sorted else ""

        # Set visible: si hay varios, mostrar uno (o podrías poner "Varios sets")
        set_display = ""
        if g["set_codes"]:
            if len(g["set_codes"]) == 1:
                set_display = next(iter(g["set_codes"]))
            else:
                # puedes cambiar este texto si quieres mostrar algo distinto
                set_display = "Varios sets"

        # Determinar precio a mostrar:
        # 1) Preferir siempre el mejor precio NO FOIL
        if g["best_price_clp_nonfoil"] is not None and g["best_price_clp_nonfoil"] > 0:
            price_clp_val = g["best_price_clp_nonfoil"]
            price_display = format_clp(price_clp_val)
            price_usd_ref_str = (
                f"{g['best_price_usd_nonfoil']:.2f}" if g["best_price_usd_nonfoil"] is not None else ""
            )
        # 2) Si no hay no foil con precio, usar el mejor en general
        elif g["best_price_clp"] is not None and g["best_price_clp"] > 0:
            price_clp_val = g["best_price_clp"]
            price_display = format_clp(price_clp_val)
            price_usd_ref_str = (
                f"{g['best_price_usd_ref']:.2f}" if g["best_price_usd_ref"] is not None else ""
            )
        else:
            price_display = "Consultar"
            price_usd_ref_str = ""

        # isFoil para la tarjeta principal:
        # - True si TODAS las copias son foil
        # - False si hay mezcla o todas no foil
        is_foil_card = g["hasFoil"] and not g["hasNonFoil"]

        cards.append(
            {
                "name": g["name"],
                "set": set_display,
                "lang": lang_display,
                "condition": g["condition"],
                "isFoil": is_foil_card,
                "hasFoil": g["hasFoil"],
                "hasNonFoil": g["hasNonFoil"],
                "format": g["format"],
                "quantity": g["quantity"],
                "price": price_display,
                "priceUsdRef": price_usd_ref_str,
                "imageFile": g["imageFile"],
                "copies": g["copies"],
            }
        )

    # Ordenar por nombre
    cards.sort(key=lambda c: c["name"].lower())
    return cards





# ========== HTML CON PAGINACIÓN + MODAL GRANDE ==========

def build_full_html(cards: List[Dict]) -> str:
    """
    Construye el HTML completo de la tienda, con:
    - Paginación en el front.
    - Modal grande para ver copias.
    - Agrupación de cartas por nombre/set/condición/foil/formato ignorando idioma.
    - Botón flotante de WhatsApp.
    - Sin chip FOIL ni etiquetas de estado en la card (NM, LP, etc.).
    """
    cards_json = json.dumps(cards, ensure_ascii=False)
    template = f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8" />
    <title>Tienda de Cartas Magic</title>
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <style>
        :root {{
            --bg-color: #050816;
            --bg-card: #0b1020;
            --bg-card-hover: #151b33;
            --accent: #08d9d6;
            --accent-soft: rgba(8, 217, 214, 0.2);
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --danger: #f97373;
            --border-radius-lg: 18px;
            --border-radius-md: 12px;
            --border-radius-sm: 8px;
            --shadow-soft: 0 18px 40px rgba(15, 23, 42, 0.65);
            --shadow-soft-sm: 0 10px 22px rgba(15, 23, 42, 0.7);
            --shadow-hard: 0 0 0 1px rgba(15, 23, 42, 0.95), 0 24px 60px rgba(0, 0, 0, 0.95);
            --input-bg: #020617;
            --input-border: rgba(148, 163, 184, 0.45);
            --pill-bg: rgba(15, 23, 42, 0.9);
            --pill-border: rgba(148, 163, 184, 0.5);
            --nav-bg: rgba(15, 23, 42, 0.95);
        }}

        * {{
            box-sizing: border-box;
        }}

        html,
        body {{
            margin: 0;
            padding: 0;
            min-height: 100%;
            background-color: #020617;
            background-image:
                radial-gradient(circle at top left, rgba(37, 99, 235, 0.15), transparent 55%),
                radial-gradient(circle at bottom right, rgba(8, 217, 214, 0.12), transparent 60%);
            color: var(--text-main);
            font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        }}

        body {{
            display: flex;
            justify-content: center;
            padding: 0;
        }}

        .page-shell {{
            width: 100%;
            max-width: 1240px;
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            background: radial-gradient(circle at 0 0, rgba(15, 23, 42, 0.95), rgba(15, 23, 42, 0.97));
            box-shadow: var(--shadow-hard);
        }}

        header {{
            position: sticky;
            top: 0;
            z-index: 40;
            backdrop-filter: blur(18px);
            background: linear-gradient(
                to bottom,
                rgba(15, 23, 42, 0.98),
                rgba(15, 23, 42, 0.94),
                rgba(15, 23, 42, 0.9)
            );
            border-bottom: 1px solid rgba(15, 23, 42, 0.95);
        }}

        .header-inner {{
            max-width: 1200px;
            margin: 0 auto;
            padding: 0.65rem 1.2rem;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 1rem;
        }}

        .brand {{
            display: flex;
            align-items: center;
            gap: 0.75rem;
        }}

        .brand-icon {{
            width: 32px;
            height: 32px;
            border-radius: 999px;
            background: radial-gradient(circle at 30% 25%, #f97316, #e11d48);
            box-shadow: 0 0 0 2px rgba(15, 23, 42, 0.9), 0 18px 30px rgba(15, 23, 42, 0.85);
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 800;
            font-size: 1.1rem;
            color: #fefce8;
        }}

        .brand-text-main {{
            font-weight: 700;
            letter-spacing: 0.06em;
            text-transform: uppercase;
            font-size: 0.95rem;
        }}

        .brand-text-sub {{
            font-size: 0.75rem;
            color: var(--text-muted);
        }}

        .brand-text-wrapper {{
            display: flex;
            flex-direction: column;
            gap: 0.15rem;
        }}

        .toolbar-top {{
            display: flex;
            flex-direction: column;
            gap: 0.45rem;
            flex: 1;
        }}

        .toolbar-top-row {{
            display: flex;
            gap: 0.5rem;
            align-items: center;
            justify-content: flex-end;
            flex-wrap: wrap;
        }}

        .toolbar-pill {{
            background: rgba(15, 23, 42, 0.95);
            border-radius: 999px;
            padding: 0.25rem 0.6rem;
            border: 1px solid rgba(51, 65, 85, 0.9);
            display: inline-flex;
            align-items: center;
            gap: 0.35rem;
            font-size: 0.7rem;
            color: var(--text-muted);
        }}

        .toolbar-pill strong {{
            color: var(--accent);
        }}

        .toolbar-stats {{
            display: flex;
            align-items: center;
            gap: 0.5rem;
            justify-content: flex-end;
            flex-wrap: wrap;
        }}

        .counter-strong {{
            font-weight: 600;
            color: var(--text-main);
        }}

        main {{
            flex: 1;
            max-width: 1200px;
            margin: 0 auto;
            padding: 0.75rem 1.2rem 1.2rem;
        }}

        .search-card {{
            background: radial-gradient(circle at top left, rgba(8, 47, 73, 0.6), transparent 60%),
                        radial-gradient(circle at bottom right, rgba(15, 23, 42, 0.95), transparent 55%),
                        rgba(15, 23, 42, 0.98);
            border-radius: var(--border-radius-lg);
            padding: 0.85rem 0.9rem 0.8rem;
            border: 1px solid rgba(15, 23, 42, 0.95);
            box-shadow: var(--shadow-soft-sm);
            margin-bottom: 0.85rem;
        }}

        .search-header {{
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            gap: 0.75rem;
            margin-bottom: 0.65rem;
        }}

        .search-title {{
            font-size: 0.95rem;
            font-weight: 600;
        }}

        .search-sub {{
            font-size: 0.75rem;
            color: var(--text-muted);
        }}

        .search-input-wrapper {{
            position: relative;
            display: flex;
            align-items: center;
            margin-top: 0.35rem;
        }}

        .search-input {{
            width: 100%;
            padding: 0.5rem 0.65rem 0.5rem 2.0rem;
            border-radius: 999px;
            border: 1px solid var(--input-border);
            background: linear-gradient(to right, #020617, #020617);
            color: var(--text-main);
            font-size: 0.85rem;
            outline: none;
            box-shadow: 0 0 0 1px rgba(15, 23, 42, 0.9);
        }}

        .search-input::placeholder {{
            color: rgba(148, 163, 184, 0.7);
        }}

        .search-icon {{
            position: absolute;
            left: 0.7rem;
            width: 1rem;
            height: 1rem;
            opacity: 0.85;
            pointer-events: none;
        }}

        .search-hint {{
            margin-top: 0.3rem;
            font-size: 0.7rem;
            color: var(--text-muted);
        }}

        .search-hint strong {{
            color: var(--accent);
        }}

        .cards-section-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 0.6rem;
            gap: 0.75rem;
        }}

        .cards-section-title {{
            font-size: 0.85rem;
            font-weight: 600;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.08em;
        }}

        .cards-section-sub {{
            font-size: 0.75rem;
            color: var(--text-muted);
        }}

        .pagination-info {{
            font-size: 0.75rem;
            color: var(--text-muted);
        }}

        .cards-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(176px, 1fr));
            gap: 0.8rem;
        }}

        .card {{
            background: radial-gradient(circle at top left, rgba(8, 217, 214, 0.08), transparent 55%),
                        radial-gradient(circle at bottom right, rgba(59, 130, 246, 0.1), transparent 55%),
                        var(--bg-card);
            border-radius: var(--border-radius-lg);
            box-shadow: var(--shadow-soft);
            padding: 0.65rem 0.65rem 0.75rem;
            display: flex;
            flex-direction: column;
            border: 1px solid rgba(15, 23, 42, 0.8);
            position: relative;
            overflow: hidden;
        }}

        .card::before {{
            content: "";
            position: absolute;
            inset: 0;
            background: radial-gradient(circle at top left, rgba(8, 217, 214, 0.15), transparent 55%),
                        radial-gradient(circle at bottom right, rgba(59, 130, 246, 0.2), transparent 60%);
            opacity: 0;
            pointer-events: none;
            transition: opacity 0.2s ease-out;
        }}

        .card:hover::before {{
            opacity: 1;
        }}

        .card:hover {{
            background: var(--bg-card-hover);
            transform: translateY(-1px);
            transition: transform 0.12s ease-out, background 0.15s ease-out;
        }}

        .card-image-wrapper {{
            border-radius: 14px;
            overflow: hidden;
            aspect-ratio: 3 / 4;
            margin-bottom: 0.5rem;
            border: 1px solid rgba(15, 23, 42, 0.9);
            background-color: #020617;
            background-image: radial-gradient(circle at top, #020617 0, #020617 30%, #020617 100%);
            position: relative;
        }}

        .card-image-wrapper img {{
            width: 100%;
            height: 100%;
            object-fit: cover;
            display: block;
        }}

        .card-body {{
            display: flex;
            flex-direction: column;
            gap: 0.45rem;
            margin-top: 0.25rem;
        }}

        .card-name {{
            font-size: 0.95rem;
            font-weight: 600;
            line-height: 1.2;
        }}

        .card-tags {{
            display: flex;
            flex-wrap: wrap;
            gap: 0.35rem;
            color: var(--text-muted);
        }}

        .card-tag {{
            padding: 0.12rem 0.45rem;
            border-radius: 999px;
            border: 1px solid rgba(148, 163, 184, 0.5);
            background: rgba(15, 23, 42, 0.9);
            font-size: 0.7rem;
        }}

        .card-footer {{
            display: flex;
            align-items: baseline;
            justify-content: space-between;
            margin-top: 0.3rem;
        }}

        .price-main {{
            font-size: 0.95rem;
            font-weight: 600;
        }}

        .price-ref {{
            font-size: 0.7rem;
            color: var(--text-muted);
        }}

        .qty-pill {{
            padding: 0.18rem 0.7rem;
            border-radius: 999px;
            background: var(--pill-bg);
            border: 1px solid var(--pill-border);
            font-size: 0.7rem;
            color: var(--text-muted);
            cursor: pointer;
            transition:
                background 0.15s ease,
                color 0.15s ease,
                border-color 0.15s ease;
        }}

        .qty-pill:hover {{
            background: rgba(8, 217, 214, 0.08);
            color: #e5e7eb;
            border-color: var(--accent-soft);
        }}


        .empty-state {{
            margin-top: 1.5rem;
            padding: 1.2rem;
            border-radius: var(--border-radius-lg);
            border: 1px dashed rgba(148, 163, 184, 0.7);
            background: rgba(15, 23, 42, 0.95);
            text-align: center;
            font-size: 0.85rem;
            color: var(--text-muted);
        }}

        .pagination-container {{
            display: flex;
            justify-content: center;
            margin-top: 0.75rem;
            gap: 0.4rem;
        }}

        .page-btn {{
            border-radius: 999px;
            border: 1px solid rgba(148, 163, 184, 0.6);
            background: rgba(15, 23, 42, 0.95);
            color: var(--text-main);
            padding: 0.15rem 0.55rem;
            font-size: 0.75rem;
            cursor: pointer;
        }}

        .page-btn.active {{
            background: var(--accent);
            border-color: var(--accent);
            color: #020617;
            font-weight: 600;
        }}

        .page-btn:disabled {{
            opacity: 0.3;
            cursor: default;
        }}

        footer {{
            margin-top: auto;
            border-top: 1px solid rgba(15, 23, 42, 0.9);
            background: rgba(15, 23, 42, 0.97);
        }}

        .footer-inner {{
            max-width: 1200px;
            margin: 0 auto;
            padding: 0.55rem 1.2rem;
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 0.7rem;
            color: var(--text-muted);
        }}

        /* ===== MODAL GRANDE DE COPIAS ===== */

        .copies-modal {{
            position: fixed;
            inset: 0;
            z-index: 999;
            display: none;
        }}

        .copies-modal-backdrop {{
            position: absolute;
            inset: 0;
            background: rgba(15, 23, 42, 0.88);
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 1.2rem;
        }}

        .copies-modal-dialog {{
            max-width: 960px;
            width: 100%;
            max-height: 90vh;
            background: radial-gradient(circle at top left, rgba(15, 23, 42, 0.98), rgba(15, 23, 42, 0.97));
            border-radius: 18px;
            border: 1px solid rgba(148, 163, 184, 0.5);
            box-shadow: 0 24px 80px rgba(0, 0, 0, 0.9);
            display: flex;
            flex-direction: column;
            padding: 0.9rem;
            gap: 0.6rem;
        }}

        .copies-modal-header {{
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            gap: 0.75rem;
        }}

        .copies-modal-title {{
            font-size: 1.05rem;
            font-weight: 600;
        }}

        .copies-modal-meta {{
            margin-top: 0.2rem;
            font-size: 0.8rem;
            color: var(--text-muted);
        }}

        .copies-modal-close {{
            border: none;
            background: rgba(15, 23, 42, 0.95);
            border-radius: 999px;
            padding: 0.25rem 0.55rem;
            font-size: 0.85rem;
            color: var(--text-muted);
            cursor: pointer;
            border: 1px solid rgba(148, 163, 184, 0.5);
        }}

        .copies-modal-close:hover {{
            background: rgba(30, 64, 175, 0.8);
            color: #e5e7eb;
        }}

        .copies-modal-body {{
            flex: 1;
            margin-top: 0.3rem;
            overflow-y: auto;
        }}

        .copies-modal-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 0.6rem;
        }}

        .copies-modal-item {{
            background: rgba(15, 23, 42, 0.95);
            border-radius: 12px;
            border: 1px solid rgba(30, 64, 175, 0.7);
            overflow: hidden;
            box-shadow: var(--shadow-soft-sm);
        }}

        .copies-modal-imgwrap {{
            position: relative;
            aspect-ratio: 3 / 4;
            background: #020617;
        }}

        .copies-modal-imgwrap img {{
            width: 100%;
            height: 100%;
            object-fit: cover;
            display: block;
        }}

        .copies-modal-qty {{
            position: absolute;
            bottom: 0.2rem;
            right: 0.25rem;
            padding: 0.1rem 0.45rem;
            border-radius: 999px;
            background: rgba(15, 23, 42, 0.95);
            font-size: 0.7rem;
        }}

        .copies-modal-lang {{
            position: absolute;
            top: 0.2rem;
            left: 0.25rem;
            padding: 0.1rem 0.45rem;
            border-radius: 999px;
            background: rgba(30, 64, 175, 0.95);
            font-size: 0.7rem;
        }}

        /* ===== BOTÓN FLOTANTE WHATSAPP ===== */

        .whatsapp-fab {{
            position: fixed;
            bottom: 1.4rem;
            right: 1.4rem;
            z-index: 1000;
            display: inline-flex;
            align-items: center;
            gap: 0.5rem;
            padding: 0.55rem 0.9rem;
            border-radius: 999px;
            background: #22c55e;
            color: #ecfdf5;
            font-size: 0.8rem;
            text-decoration: none;
            box-shadow: 0 18px 35px rgba(22, 163, 74, 0.7);
        }}

        .whatsapp-fab-icon {{
            width: 1.4rem;
            height: 1.4rem;
            border-radius: 999px;
            background: #16a34a;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            font-size: 1rem;
        }}

        .whatsapp-fab-text {{
            white-space: nowrap;
        }}

        @media (max-width: 768px) {{
            .header-inner {{
                flex-direction: column;
                align-items: flex-start;
            }}

            .cards-grid {{
                grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
            }}

            .search-card {{
                margin-bottom: 0.7rem;
            }}

            .copies-modal-dialog {{
                max-height: 95vh;
                padding: 0.7rem;
            }}

            .copies-modal-grid {{
                grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            }}
        }}

        @media (max-width: 640px) {{
            .whatsapp-fab {{
                padding: 0.55rem;
            }}
            .whatsapp-fab-text {{
                display: none;
            }}
        }}
    </style>
</head>
<body>
<div class="page-shell">
    <header>
        <div class="header-inner">
            <div class="brand">
                <div class="brand-icon">M</div>
                <div class="brand-text-wrapper">
                    <div class="brand-text-main">El Castillo Magic</div>
                    <div class="brand-text-sub">Catálogo de cartas individuales · Chile</div>
                </div>
            </div>
            <div class="toolbar-top">
                <div class="toolbar-top-row">
                    <div class="toolbar-pill">
                        <span>Filtro por nombre, edición, formato o idioma.</span>
                    </div>
                </div>
                <div class="toolbar-stats">
                    <div class="toolbar-pill">
                        Cartas visibles:
                        <span id="visibleCount" class="counter-strong">0</span>
                    </div>
                    <div class="toolbar-pill">
                        Total catálogo:
                        <span id="totalCount" class="counter-strong">0</span>
                    </div>
                </div>
            </div>
        </div>
    </header>

    <main>
        <section class="search-card">
            <div class="search-header">
                <div>
                    <div class="search-title">Busca tu carta</div>
                    <div class="search-sub">Puedes buscar por nombre, set, formato (Modern, Commander, etc.) o idioma.</div>
                </div>
            </div>
            <div class="search-input-wrapper">
                <span class="search-icon">🔍</span>
                <input
                    id="searchInput"
                    class="search-input"
                    type="text"
                    placeholder="Ej: Lightning Bolt, Double Masters, Modern, Español..."
                    autocomplete="off"
                />
            </div>
            <div class="search-hint">
                <strong>Tip:</strong> escribe parte del nombre o el código de la edición (ej: MH2, 2XM, MPS).
            </div>
        </section>

        <section class="cards-section">
            <div class="cards-section-header">
                <div>
                    <div class="cards-section-title">Catálogo disponible</div>
                    <div class="cards-section-sub" id="pageInfo"></div>
                </div>
            </div>

            <section id="cardsSection">
                <div id="cardsContainer" class="cards-grid"></div>
                <div id="emptyState" class="empty-state" style="display:none;">
                    No encontramos cartas para tu búsqueda.
                    <br />
                    Prueba con otro nombre, edición o <strong>limpia el filtro</strong>.
                </div>
                <div id="pagination" class="pagination-container"></div>
            </section>
        </section>
    </main>

    <!-- Modal grande de copias -->
    <div id="copiesModal" class="copies-modal" style="display:none;">
        <div class="copies-modal-backdrop" id="copiesModalBackdrop">
            <div class="copies-modal-dialog">
                <div class="copies-modal-header">
                    <div>
                        <div id="copiesModalTitle" class="copies-modal-title"></div>
                        <div id="copiesModalMeta" class="copies-modal-meta"></div>
                    </div>
                    <button id="copiesModalClose" class="copies-modal-close" type="button">✕</button>
                </div>
                <div id="copiesModalBody" class="copies-modal-body"></div>
            </div>
        </div>
    </div>

    <!-- Botón flotante de WhatsApp -->
    <a
        href="https://wa.me/56990590045?text=Hola%20me%20interesa%20una%20carta%20de%20tu%20tienda"
        class="whatsapp-fab"
        target="_blank"
        rel="noopener noreferrer"
    >
        <span class="whatsapp-fab-icon">💬</span>
        <span class="whatsapp-fab-text">Si te interesa una carta o si ves algun detalle en el sitio, por favor Contáctame</span>
    </a>

    <footer>
        <div class="footer-inner">
            <span>Generado automáticamente desde inventario_cartas.csv.</span>
            <span>El Castillo Magic &copy; 2025</span>
        </div>
    </footer>
</div>

<script>
    const IMAGE_BASE_PATH = "images";
    const PAGE_SIZE = 30;
    const cardsData = {cards_json};
    const IS_COARSE_POINTER = window.matchMedia && window.matchMedia("(pointer: coarse)").matches;

    let filteredCards = [...cardsData];
    let currentPage = 1;

    const $ = (id) => document.getElementById(id);

    function debounce(fn, delay) {{
        let timer = null;
        return (...args) => {{
            if (timer) clearTimeout(timer);
            timer = setTimeout(() => fn(...args), delay);
        }};
    }}

    function openCopiesModal(card) {{
        const modal = document.getElementById("copiesModal");
        const titleEl = document.getElementById("copiesModalTitle");
        const metaEl = document.getElementById("copiesModalMeta");
        const bodyEl = document.getElementById("copiesModalBody");

        if (!modal || !titleEl || !metaEl || !bodyEl) return;

        titleEl.textContent = card.name || "";

        const metaParts = [];
        if (card.set) metaParts.push((card.set || "").toUpperCase());
        if (card.format) metaParts.push(card.format);
        if (card.lang) metaParts.push((card.lang || "").toUpperCase());
        if (card.condition) metaParts.push(card.condition);
        metaParts.push("Stock: " + (card.quantity || 1));
        metaEl.textContent = metaParts.join(" · ");

        bodyEl.innerHTML = "";

        const grid = document.createElement("div");
        grid.className = "copies-modal-grid";

        const copies = (Array.isArray(card.copies) && card.copies.length)
            ? card.copies
            : [{{ imageFile: card.imageFile, quantity: card.quantity, lang: card.lang }}];

        for (const copy of copies) {{
            const item = document.createElement("div");
            item.className = "copies-modal-item";

            const imgWrap = document.createElement("div");
            imgWrap.className = "copies-modal-imgwrap";

            const img = document.createElement("img");
            img.loading = "lazy";
            img.alt = card.name + " - copia";
            img.src = IMAGE_BASE_PATH + "/" + encodeURI(copy.imageFile || card.imageFile || "");
            imgWrap.appendChild(img);

            const qtyBadge = document.createElement("div");
            qtyBadge.className = "copies-modal-qty";
            qtyBadge.textContent = "x" + (copy.quantity || 1);
            imgWrap.appendChild(qtyBadge);

            if (copy.lang) {{
                const langBadge = document.createElement("div");
                langBadge.className = "copies-modal-lang";
                langBadge.textContent = (copy.lang || "").toUpperCase();
                imgWrap.appendChild(langBadge);
            }}

            item.appendChild(imgWrap);
            grid.appendChild(item);
        }}

        bodyEl.appendChild(grid);
        modal.style.display = "block";
        document.body.style.overflow = "hidden";
    }}

    function closeCopiesModal() {{
        const modal = document.getElementById("copiesModal");
        if (modal) {{
            modal.style.display = "none";
        }}
        document.body.style.overflow = "";
    }}

        function buildCardElement(card) {{
        const article = document.createElement("article");
        article.className = "card";

        const hasMultiple = Array.isArray(card.copies) && card.copies.length > 1;
        if (hasMultiple) {{
            article.classList.add("has-multiple");
        }}

        const imageWrapper = document.createElement("div");
        imageWrapper.className = "card-image-wrapper";

        const mainImageFile = hasMultiple && card.copies[0].imageFile
            ? card.copies[0].imageFile
            : card.imageFile;

        const img = document.createElement("img");
        img.loading = "lazy";
        img.alt = card.name || "Carta Magic";
        img.src = IMAGE_BASE_PATH + "/" + encodeURI(mainImageFile || card.imageFile || "");
        imageWrapper.appendChild(img);

        if (card.isFoil) {{
            const foilChip = document.createElement("div");
            foilChip.className = "foil-chip";
            foilChip.textContent = "FOIL";
            imageWrapper.appendChild(foilChip);
        }}

        const body = document.createElement("div");
        body.className = "card-body";

        const nameEl = document.createElement("div");
        nameEl.className = "card-name";
        nameEl.textContent = card.name;
        body.appendChild(nameEl);

        const tags = document.createElement("div");
        tags.className = "card-tags";

        const setTag = document.createElement("span");
        setTag.className = "tag";
        setTag.textContent = card.setName || card.setCode || "Set desconocido";
        tags.appendChild(setTag);

        if (card.format) {{
            const formatTag = document.createElement("span");
            formatTag.className = "tag";
            formatTag.textContent = card.format.toUpperCase();
            tags.appendChild(formatTag);
        }}

        if (card.lang) {{
            const tagLang = document.createElement("span");
            tagLang.className = "tag";
            tagLang.textContent = (card.lang || "").toUpperCase();
            tags.appendChild(tagLang);
        }}

        body.appendChild(tags);

        const footer = document.createElement("div");
        footer.className = "card-footer";

        const priceBox = document.createElement("div");
        const priceMain = document.createElement("div");
        priceMain.className = "price-main";
        priceMain.textContent = "$ " + card.price;
        priceBox.appendChild(priceMain);

        if (card.priceUsdRef) {{
            const priceRef = document.createElement("div");
            priceRef.className = "price-ref";
            priceRef.textContent = "Ref: USD " + card.priceUsdRef;
            priceBox.appendChild(priceRef);
        }}

        footer.appendChild(priceBox);

        // ============================
        // 🔹 Nuevo botón "Ver stock (n)"
        // ============================
        const qtyButton = document.createElement("button");
        qtyButton.type = "button";
        qtyButton.className = "qty-pill";
        qtyButton.textContent = "Ver stock (" + (card.quantity || 1) + ")";

        qtyButton.addEventListener("click", (event) => {{
            event.stopPropagation();
            openCopiesModal(card);
        }});

        footer.appendChild(qtyButton);
        body.appendChild(footer);

        article.appendChild(imageWrapper);
        article.appendChild(body);

        // ❌ Eliminado: abrir modal con hover o click en toda la carta

        return article;
    }}


    function renderCards() {{
        const container = document.getElementById("cardsContainer");
        const emptyState = document.getElementById("emptyState");
        const totalCountEl = document.getElementById("totalCount");
        const visibleCountEl = document.getElementById("visibleCount");
        const pageInfo = document.getElementById("pageInfo");

        totalCountEl.textContent = String(cardsData.length);

        if (!filteredCards.length) {{
            container.innerHTML = "";
            emptyState.style.display = "block";
            visibleCountEl.textContent = "0";
            pageInfo.textContent = "";
            document.getElementById("pagination").innerHTML = "";
            return;
        }}

        emptyState.style.display = "none";

        const totalPages = Math.max(1, Math.ceil(filteredCards.length / PAGE_SIZE));
        if (currentPage > totalPages) {{
            currentPage = totalPages;
        }}

        const startIdx = (currentPage - 1) * PAGE_SIZE;
        const endIdx = startIdx + PAGE_SIZE;
        const pageCards = filteredCards.slice(startIdx, endIdx);

        container.innerHTML = "";
        for (const card of pageCards) {{
            container.appendChild(buildCardElement(card));
        }}

        visibleCountEl.textContent = String(pageCards.length);
        pageInfo.textContent = "Mostrando página " + currentPage + " de " + totalPages;

        renderPagination(totalPages);
    }}

    function renderPagination(totalPages) {{
        const pagination = document.getElementById("pagination");
        pagination.innerHTML = "";

        if (totalPages <= 1) {{
            return;
        }}

        const prevBtn = document.createElement("button");
        prevBtn.className = "page-btn";
        prevBtn.textContent = "←";
        prevBtn.disabled = currentPage === 1;
        prevBtn.onclick = () => {{
            if (currentPage > 1) {{
                currentPage--;
                renderCards();
            }}
        }};
        pagination.appendChild(prevBtn);

        const maxToShow = 7;
        let start = Math.max(1, currentPage - 3);
        let end = Math.min(totalPages, start + maxToShow - 1);
        if (end - start < maxToShow - 1) {{
            start = Math.max(1, end - maxToShow + 1);
        }}

        if (start > 1) {{
            const first = document.createElement("button");
            first.className = "page-btn";
            first.textContent = "1";
            first.onclick = () => {{
                currentPage = 1;
                renderCards();
            }};
            pagination.appendChild(first);

            if (start > 2) {{
                const dots = document.createElement("span");
                dots.className = "page-btn";
                dots.textContent = "…";
                pagination.appendChild(dots);
            }}
        }}

        for (let i = start; i <= end; i++) {{
            const btn = document.createElement("button");
            btn.className = "page-btn" + (i === currentPage ? " active" : "");
            btn.textContent = String(i);
            if (i !== currentPage) {{
                btn.onclick = () => {{
                    currentPage = i;
                    renderCards();
                }};
            }} else {{
                btn.disabled = true;
            }}
            pagination.appendChild(btn);
        }}

        if (end < totalPages) {{
            if (end < totalPages - 1) {{
                const dots = document.createElement("span");
                dots.className = "page-btn";
                dots.textContent = "…";
                pagination.appendChild(dots);
            }}

            const last = document.createElement("button");
            last.className = "page-btn";
            last.textContent = String(totalPages);
            last.onclick = () => {{
                currentPage = totalPages;
                renderCards();
            }};
            pagination.appendChild(last);
        }}

        const nextBtn = document.createElement("button");
        nextBtn.className = "page-btn";
        nextBtn.textContent = "→";
        nextBtn.disabled = currentPage === totalPages;
        nextBtn.onclick = () => {{
            if (currentPage < totalPages) {{
                currentPage++;
                renderCards();
            }}
        }};
        pagination.appendChild(nextBtn);
    }}

    function applyFilters() {{
        const input = document.getElementById("searchInput");
        const query = (input.value || "").trim().toLowerCase();

        if (!query) {{
            filteredCards = [...cardsData];
        }} else {{
            filteredCards = cardsData.filter((card) => {{
                const haystack = [
                    card.name || "",
                    card.set || "",
                    card.format || "",
                    card.lang || "",
                    card.condition || ""
                ]
                    .join(" ")
                    .toLowerCase();

                return haystack.includes(query);
            }});
        }}

        currentPage = 1;
        renderCards();
    }}

    function init() {{
        const input = document.getElementById("searchInput");
        input.addEventListener("input", debounce(applyFilters, 200));

        const modalClose = document.getElementById("copiesModalClose");
        const modalBackdrop = document.getElementById("copiesModalBackdrop");

        if (modalClose) {{
            modalClose.addEventListener("click", closeCopiesModal);
        }}
        if (modalBackdrop) {{
            modalBackdrop.addEventListener("click", (e) => {{
                if (e.target === modalBackdrop) {{
                    closeCopiesModal();
                }}
            }});
        }}

        filteredCards = [...cardsData];
        currentPage = 1;
        renderCards();
    }}

    document.addEventListener("DOMContentLoaded", init);
</script>
</body>
</html>
"""
    return template



# ========== COPIA DE IMÁGENES ==========

def copy_images():
    """Copia todas las imágenes desde PROCESADAS_DIR a DEPLOY_IMAGES_DIR."""
    if not PROCESADAS_DIR.exists():
        raise SystemExit(f"[ERROR] No existe PROCESADAS_DIR: {PROCESADAS_DIR}")

    DEPLOY_IMAGES_DIR.mkdir(parents=True, exist_ok=True)

    # Borrar imágenes anteriores
    for f in DEPLOY_IMAGES_DIR.iterdir():
        if f.is_file():
            f.unlink()

    count = 0
    for root, _, files in os.walk(PROCESADAS_DIR):
        for fname in files:
            src = Path(root) / fname
            if not src.is_file():
                continue
            ext = src.suffix.lower()
            if ext not in {".jpg", ".jpeg", ".png"}:
                continue
            dst = DEPLOY_IMAGES_DIR / src.name
            shutil.copy2(src, dst)
            count += 1

    print(f"[INFO] Se copiaron {count} imágenes a {DEPLOY_IMAGES_DIR}")


# ========== GIT: ADD / COMMIT / PUSH ==========

# ========== GIT: ADD / COMMIT / PUSH ==========

def git_commit_and_push():
    """
    Hace git add / commit / push en el repositorio definido en GIT_REPO_DIR
    (por defecto, DEPLOY_DIR).
    """
    if not (GIT_REPO_DIR / ".git").exists():
        print(f"[WARN] {GIT_REPO_DIR} no parece ser un repositorio git, se omite git push.")
        return

    print("[INFO] Revisando cambios en el repo...")
    status_code = run_cmd(["git", "status", "--porcelain"], cwd=GIT_REPO_DIR)
    if status_code != 0:
        print("[WARN] git status falló, revisa la configuración de git.")
        return

    # Vuelta extra para leer salida y decidir si hay cambios
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(GIT_REPO_DIR),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print("[WARN] No se pudo obtener el estado del repositorio.")
        return

    changed = result.stdout.strip()
    if not changed:
        print("[INFO] No hay cambios en el repositorio, no se hace commit.")
        return

    run_cmd(["git", "add", "."], cwd=GIT_REPO_DIR)
    commit_msg = f"Update tienda {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    run_cmd(["git", "commit", "-m", commit_msg], cwd=GIT_REPO_DIR)
    run_cmd(["git", "push"], cwd=GIT_REPO_DIR)
    print("[OK] Cambios enviados a GitHub.")



# ========== MAIN ==========

def main():
    """
    Genera el HTML y copia las imágenes usando el inventario YA construido
    y con precios YA actualizados.

    IMPORTANTE:
    - Este script YA NO ejecuta auto_etiquetar_renombrar.py
      ni construir_inventario_desde_fotos.py.
    - Esos pasos deben hacerse antes (por ejemplo, desde el .bat
      actualizar_tienda_magic.bat en modo "one click").
    """
    # 1) Cargar inventario existente (debe incluir columnas de precio)
    rows = load_inventory(INVENTORY_CSV)

    # 2) Preparar datos para el frontend (agrupando copias e idiomas)
    cards = prepare_cards_for_frontend(rows)

    # 3) Construir HTML
    full_html = build_full_html(cards)
    DEPLOY_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_HTML.write_text(full_html, encoding="utf-8")
    print(f"[INFO] HTML generado en: {OUTPUT_HTML}")

    # 4) Copiar imágenes a la carpeta del sitio
    copy_images()

    # 5) git add / commit / push
    git_commit_and_push()

    print("\n[OK] Flujo completo terminado.\n")




if __name__ == "__main__":
    main()
