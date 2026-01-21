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

from logger_tienda import get_logger, log_info, log_exception
from config_tienda import (
    PROJECT_ROOT,
    PROCESADAS_DIR,
    DEPLOY_DIR,
    INVENTORY_CSV,
    OUTPUT_HTML,
    DEPLOY_IMAGES_DIR,
    GIT_REPO_DIR,
)
import os  # ya lo tienes
from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

# NUEVO: inventarios por vendedor
SELLER_INVENTORIES_DIR: Path = INVENTORY_CSV.parent / "inventarios_vendedores"

IMAGES_REPO_DIR = Path(r"C:\Franco\Magic\tienda_web_images\images")
# URL base donde GitHub Pages sirve las imágenes del repo tienda_web_images
IMAGES_BASE_URL = "https://raw.githubusercontent.com/FrancoArenas1-1987/tienda_web_images/main/images"



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

def load_inventory(source_path: Path) -> List[Dict]:
    """
    Carga el inventario desde:

    - Un solo CSV (modo antiguo), o
    - Un directorio con varios CSV de vendedores (nuevo modo).

    Reglas:
    - Solo status 'Disponible' / 'available' / 'avail' / vacío pasan al front.
    - Solo quantity > 0.
    - Calcula price_display y normaliza is_foil.
    """
    if not source_path.exists():
        raise SystemExit(f"[ERROR] No se encontró el origen de inventario: {source_path}")

    rows: List[Dict] = []

    def process_csv(csv_path: Path):
        nonlocal rows
        with csv_path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                status_raw = (row.get("status") or "").strip()
                status = status_raw.lower()

                # Aceptamos 'disponible', 'available', 'avail' o vacío
                if status not in {"disponible", "available", "avail", ""}:
                    continue

                try:
                    quantity = int(row.get("quantity", 0))
                except ValueError:
                    quantity = 0

                if quantity <= 0:
                    continue

                row["quantity"] = quantity
                row["price_display"] = format_clp((row.get("price_clp") or "").strip())
                row["image_url"] = (row.get("image_url") or "").strip()

                # Aseguramos que los campos nuevos existan aunque vengan vacíos
                row["seller_name"] = (row.get("seller_name") or "").strip()
                row["seller_phone"] = (row.get("seller_phone") or "").strip()

                # Normalizamos status a "Disponible" (para consistencia interna)
                if status in {"disponible", "available", "avail", ""}:
                    row["status"] = "Disponible"
                else:
                    row["status"] = status_raw

                rows.append(row)

    if source_path.is_dir():
        csv_files = sorted(source_path.glob("*.csv"))
        if not csv_files:
            raise SystemExit(f"[ERROR] No se encontraron CSV de vendedores en: {source_path}")
        for csv_path in csv_files:
            process_csv(csv_path)
    else:
        # Modo antiguo (un solo inventario global)
        process_csv(source_path)

    print(f"[INFO] Inventario cargado: {len(rows)} cartas disponibles.")
    return rows



# ========== PREPARACIÓN DE CARTAS PARA EL FRONT ==========

def prepare_cards_for_frontend(rows: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """
    Transforma las filas del CSV en una estructura optimizada para el frontend.

    LÓGICA PRINCIPAL:

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
            imageFile, quantity, lang, condition, format, isFoil, priceClp, set,
            sellerName, sellerPhone.

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

        # 👇 NUEVO: nombres alternativos para búsqueda
        name_en = (row.get("name_en") or "").strip()
        name_es = (row.get("name_es") or "").strip()
        printed_name = (row.get("printed_name") or "").strip()

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

        seller_name = (row.get("seller_name") or "").strip()
        seller_phone = (row.get("seller_phone") or "").strip()

        # Nuevo: la carta se agrupa por nombre + número del vendedor
        seller_phone = (row.get("seller_phone") or "").strip()
        key = f"{name.lower()}__{seller_phone}"


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
                "seller_name": row.get("seller_name", "").strip(),
                "seller_phone": row.get("seller_phone", "").strip(),
                # 👇 NUEVO: conjunto de alias para búsqueda
                "search_aliases": set(),

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

        # 👇 NUEVO: agregar alias de nombres para búsqueda bilingüe
        alias_set = g["search_aliases"]
        if name:
            alias_set.add(name)
        if name_en:
            alias_set.add(name_en)
        if name_es:
            alias_set.add(name_es)
        if printed_name:
            alias_set.add(printed_name)


        # Actualizar condición/format dominante si corresponde
        r_new = condition_rank(condition)
        if r_new > g.get("condition_rank", 0):
            g["condition"] = condition
            g["format"] = fmt
            g["condition_rank"] = r_new

        # Guardar detalle de copia para el modal, incluyendo vendedor
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
                "sellerName": seller_name,
                "sellerPhone": seller_phone,
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
                set_display = "Varios sets"

                # Determinar precio a mostrar:
        # 1) Preferir siempre el mejor precio NO FOIL
        if g["best_price_clp_nonfoil"] is not None and g["best_price_clp_nonfoil"] > 0:
            price_clp_val = g["best_price_clp_nonfoil"]
            price_display = format_clp(price_clp_val)
            price_usd_ref_str = (
                f"{g['best_price_usd_nonfoil']:.2f}" if g["best_price_usd_nonfoil"] is not None else ""
            )
        # 2) Si no hay no foil con precio, usar el mejor en general (ej: todas foil o precio manual)
        elif g["best_price_clp"] is not None and g["best_price_clp"] > 0:
            price_clp_val = g["best_price_clp"]
            price_display = format_clp(price_clp_val)
            price_usd_ref_str = (
                f"{g['best_price_usd_ref']:.2f}" if g["best_price_usd_ref"] is not None else ""
            )
        else:
            # No hay precio numérico (CardKingdom / manual) para este grupo
            price_clp_val = None
            price_display = "Consultar"
            price_usd_ref_str = ""

        # isFoil para la tarjeta principal:
        # - True si TODAS las copias son foil
        # - False si hay mezcla o todas no foil
        is_foil_card = g["hasFoil"] and not g["hasNonFoil"]

        # ---- Texto de precio según vendedor (.env) SOLO si NO hay precio numérico
        seller_name_group = (g.get("seller_name") or "").strip()
        seller_phone_group = (g.get("seller_phone") or "").strip()

        # Hay precio real si best_price_clp tiene un valor > 0 (CardKingdom o manual)
        has_real_price = g["best_price_clp"] is not None and g["best_price_clp"] > 0

        # Por defecto, mostramos el CLP formateado (o "Consultar" si no hay)
        price_label = price_display

        # Si NO hay precio numérico y existe variable en .env para ese vendedor, usamos el texto CK
        if (not has_real_price) and seller_name_group:
            env_key = f"{seller_name_group}_CK_USD"
            env_value = os.getenv(env_key)
            if env_value:
                price_label = env_value  # ej: "CK 720, $400 mínimo por carta"



        search_aliases_str = " ".join(sorted(g.get("search_aliases", set())))
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
                "price": price_label,          # 👈 texto final (CLP o CK XXX)
                "priceUsdRef": price_usd_ref_str,
                "imageFile": g["imageFile"],
                "copies": g["copies"],
                "seller_name": seller_name_group,
                "seller_phone": seller_phone_group,
                # 👇 NUEVO
                "searchAliases": search_aliases_str,
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
    - Botón flotante de WhatsApp general.
    - Botón de WhatsApp POR COPIA dentro del modal (usa sellerPhone).
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

        .card-tag,
        .tag {{
            padding: 0.12rem 0.45rem;
            border-radius: 999px;
            border: 1px solid rgba(148, 163, 184, 0.5);
            background: rgba(15, 23, 42, 0.9);
            font-size: 0.7rem;
        }}

        .card-footer {{
            display: flex;
            flex-direction: column;   /* 👈 ahora los hijos van uno bajo otro */
            align-items: flex-start;
            margin-top: 0.35rem;
            gap: 0.25rem;   
        }}
        .card-footer-actions {{
            display: flex;
            align-items: center;
            gap: 0.4rem; /* separación entre WhatsApp y Ver stock */
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

        
                /* ===== MODAL INFORMACIÓN VENDEDORES (IA) ===== */

        .hidden {{
            display: none;
        }}

        .flex {{
            display: flex;
        }}

                .hidden {{
            display: none;
        }}

        .flex {{
            display: flex;
        }}

        #vendor-info-modal {{
            position: fixed;
            inset: 0;
            z-index: 900;
            /* OJO: aquí NO debe ir display:none; eso lo maneja la clase .hidden */
            justify-content: center;
            align-items: flex-end;
            padding: 0.75rem;
            background: rgba(15, 23, 42, 0.72);
            backdrop-filter: blur(4px);

            /* Fondo con fade-in suave */
            animation: vendorOverlayFade 0.25s ease-out forwards;
        }}

        .vendor-modal-dialog {{
            max-width: 960px;
            width: 100%;
            max-height: 90vh;
            background: radial-gradient(circle at top left, rgba(15, 23, 42, 0.98), rgba(15, 23, 42, 0.97));
            border-radius: 18px 18px 0 0;
            border: 1px solid rgba(148, 163, 184, 0.5);
            box-shadow: 0 24px 80px rgba(0, 0, 0, 0.9);
            padding: 1rem 1.25rem;
            display: flex;
            flex-direction: column;
            gap: 0.75rem;
            overflow-y: auto;

            /* Estado inicial: abajo de la pantalla, sube con rebote */
            transform: translateY(100%);
            animation: vendorSlideUpBounce 0.45s cubic-bezier(.22,1.28,.57,1) forwards;
        }}

        @keyframes vendorOverlayFade {{
            from {{ opacity: 0; }}
            to   {{ opacity: 1; }}
        }}

        @keyframes vendorSlideUpBounce {{
            0%   {{ transform: translateY(100%); }}
            70%  {{ transform: translateY(-10px); }}
            100% {{ transform: translateY(0); }}
        }}


        .vendor-modal-header {{
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            gap: 0.75rem;
            margin-bottom: 0.5rem;
        }}

        .vendor-modal-subtitle {{
            font-size: 0.85rem;
            color: var(--text-muted);
            margin-top: 0.25rem;
        }}

        .vendor-modal-body {{
            display: flex;
            flex-direction: column;
            gap: 0.75rem;
            font-size: 0.9rem;
            color: var(--text-muted);
            line-height: 1.5;
        }}

        .vendor-modal-body section h3 {{
            font-size: 1rem;
            margin-bottom: 0.3rem;
            color: var(--text-main);
        }}

        .vendor-modal-body ul,
        .vendor-modal-body ol {{
            margin: 0.25rem 0 0.25rem 1.1rem;
        }}

        /* --- PREMIUM PLAN CARDS --- */

        .plan-card {{
            background: linear-gradient(135deg, rgba(59,130,246,0.12), rgba(96,165,250,0.15));
            border: 1px solid rgba(147,197,253,0.45);
            border-radius: 14px;
            padding: 1rem 1.2rem;
            margin-bottom: 1rem;
            box-shadow: 0 8px 28px rgba(59,130,246,0.15);
            backdrop-filter: blur(6px);
            transition: 0.25s ease;
        }}

        .plan-card:hover {{
            transform: translateY(-3px);
            box-shadow: 0 12px 35px rgba(96,165,250,0.35);
            border-color: rgba(191,219,254,0.9);
        }}

        .plan-title {{
            font-size: 1.15rem;
            font-weight: 600;
            color: #e0f2fe;
            margin-bottom: 0.4rem;
        }}

        .plan-desc {{
            font-size: 0.9rem;
            color: #cbd5e1;
            margin-bottom: 0.8rem;
        }}

        .plan-price {{
            font-size: 1.1rem;
            font-weight: 600;
            color: #93c5fd;
        }}

        .plan-icon {{
            font-size: 1.4rem;
            margin-right: 0.4rem;
            color: #60a5fa;
        }}



        .vendor-modal-close {{
            background: transparent;
            border: none;
            color: var(--text-muted);
            font-size: 1.4rem;
            line-height: 1;
            cursor: pointer;
        }}

        .vendor-plans-grid {{
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 0.6rem;
            margin-top: 0.4rem;
        }}

        .vendor-plan-card {{
            border-radius: 12px;
            border: 1px solid rgba(148, 163, 184, 0.4);
            background: rgba(15, 23, 42, 0.85);
            padding: 0.7rem;
        }}

        .vendor-plan-card h4 {{
            margin: 0 0 0.2rem 0;
            font-size: 0.95rem;
            color: var(--text-main);
        }}

        .vendor-plan-price {{
            font-weight: 600;
            margin-bottom: 0.3rem;
        }}

        .vendor-launch-note {{
            margin-top: 0.6rem;
            padding: 0.6rem;
            border-radius: 10px;
            background: rgba(16, 185, 129, 0.15);
            border: 1px solid rgba(16, 185, 129, 0.35);
            font-size: 0.85rem;
        }}

        .vendor-modal-actions {{
            display: flex;
            flex-wrap: wrap;
            gap: 0.5rem;
            margin-top: 0.75rem;
        }}

        .vendor-modal-primary {{
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 0.4rem;
            padding: 0.5rem 1.1rem;
            border-radius: 999px;
            font-weight: 600;
            font-size: 0.9rem;
            border: none;
            text-decoration: none;
            cursor: pointer;
            background: var(--accent);
            color: #020617;
        }}

        .vendor-modal-secondary {{
            background: transparent;
            border: none;
            color: var(--text-muted);
            font-size: 0.75rem;
            cursor: pointer;
        }}

        @media (max-width: 900px) {{
            .vendor-plans-grid {{
                grid-template-columns: 1fr;
            }}
        }}

        @media (max-width: 640px) {{
            #vendor-info-modal {{
                padding: 0.75rem;
            }}

            .vendor-modal-dialog {{
                border-radius: 14px;
                padding: 0.75rem;
            }}
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
            background: rgba(15, 23, 42, 0.72);
            backdrop-filter: blur(4px);
            display: flex;
            align-items: center;  /* FIX */
            justify-content: center;
            padding: 0.75rem;
            animation: copiesOverlayFade 0.25s ease-out forwards;
        }}

       
                .copies-modal-dialog {{
            background: radial-gradient(circle at top left, rgba(15, 23, 42, 0.98), rgba(15, 23, 42, 0.97));
            border-radius: 18px;
            border: 1px solid rgba(148, 163, 184, 0.5);
            box-shadow: 0 24px 80px rgba(0, 0, 0, 0.9);

            /* tamaño base para varias copias */
            max-width: 1040px;
            width: min(1040px, 95vw);
            height: auto;
            max-height: 90vh;

            display: flex;
            flex-direction: column;
            padding: 0.9rem;
            gap: 0.6rem;

            transform-origin: bottom center;
            transform: translateY(80px) scale(0.96);
            animation: copiesSlideUpBounce 0.45s cubic-bezier(.22,1.28,.57,1) forwards;
        }}

        /* cuando solo hay 1 copia → hacer el modal más angosto */
        .copies-modal-dialog.single-copy {{
            max-width: 540px;
            width: min(540px, 95vw);
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
            cursor: pointer;
            color: #e5e7eb;
            font-size: 0.9rem;
        }}

        .copies-modal-close:hover {{
            background: rgba(30, 64, 175, 0.8);
            color: #e5e7eb;
        }}

        .copies-modal-body {{
            flex: 1;
            overflow-y: auto;
            padding-right: 6px;
            max-height: calc(90vh - 120px);
            display: flex;
            justify-content: center;
            width: 100%;
        }}

        .copies-modal-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
            grid-auto-rows: 380px;   /* 💥 altura uniforme por tarjeta */
            gap: 0.75rem;
            width: 100%;
            justify-items: center;
        }}


        /* si es una sola copia → siempre una columna */
        .copies-modal-dialog.single-copy .copies-modal-grid {{
            grid-template-columns: 1fr;
        }}

        .copies-modal-item {{
            background: rgba(15, 23, 42, 0.95);
            border-radius: 12px;
            border: 1px solid rgba(30, 64, 175, 0.7);
            overflow: hidden;
            box-shadow: var(--shadow-soft-sm);
            display: flex;
            flex-direction: column;

            /* altura uniforme → coincide con grid-auto-rows */
            height: 380px;
        }}


        .copies-modal-imgwrap {{
            position: relative;
            width: 100%;
            height: 100%;
            background: #020617;
            display: flex;
            align-items: center;
            justify-content: center;
        }}


        .copies-modal-imgwrap img {{
            max-width: 100%;
            max-height: 100%;
            object-fit: contain;
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

        @keyframes copiesOverlayFade {{
            from {{ opacity: 0; }}
            to   {{ opacity: 1; }}
        }}

        @keyframes copiesSlideUpBounce {{
            0%   {{ transform: translateY(80px) scale(0.96); opacity: 0; }}
            70%  {{ transform: translateY(-6px) scale(1.01); opacity: 1; }}
            100% {{ transform: translateY(0) scale(1); }}
        }}



        /* Animaciones del modal */
        @keyframes copies-backdrop-fade {{
            from {{ opacity: 0; }}
            to   {{ opacity: 1; }}
        }}

        @keyframes copies-dialog-in {{
            from {{
                opacity: 0;
                transform: translateY(12px) scale(0.97);
            }}
            to {{
                opacity: 1;
                transform: translateY(0) scale(1);
            }}
        }}

        @keyframes vendorSlideDown {{
    0%   {{ transform: translateY(0); }}
    100% {{ transform: translateY(100%); opacity: 0; }}
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

        /* Info + WhatsApp por copia */

        .copies-modal-info {{
            padding: 0.45rem 0.55rem 0.5rem;
            display: flex;
            flex-direction: column;
            gap: 0.25rem;
            font-size: 0.8rem;
        }}

        .copies-modal-meta-row {{
            display: flex;
            flex-wrap: wrap;
            gap: 0.35rem;
            color: var(--text-muted);
        }}

        .copy-pill {{
            padding: 0.1rem 0.45rem;
            border-radius: 999px;
            border: 1px solid rgba(148, 163, 184, 0.4);
            background: rgba(15, 23, 42, 0.95);
        }}

        .copy-price-row {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 0.35rem;
            margin-top: 0.2rem;
        }}

        .copy-price-main {{
            font-weight: 600;
            font-size: 0.85rem;
        }}

        .copy-whatsapp-btn {{
            border-radius: 999px;
            border: none;
            background: #22c55e;
            color: #ecfdf5;
            font-size: 0.75rem;
            padding: 0.25rem 0.5rem;
            display: inline-flex;
            align-items: center;
            gap: 0.25rem;
            cursor: pointer;
            text-decoration: none;
            white-space: nowrap;
        }}

        .copy-whatsapp-btn span {{
            font-size: 0.8rem;
        }}

        /* ===== BOTÓN FLOTANTE WHATSAPP GENERAL ===== */

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

        /* Botón flotante para publicar cartas (lado izquierdo) */
        .whatsapp-publish-fab {{
            position: fixed;
            bottom: 1.4rem;
            left: 1.4rem;
            z-index: 1000;
            display: inline-flex;
            align-items: center;
            gap: 0.5rem;
            padding: 0.55rem 0.9rem;
            border-radius: 999px;
            background: #0ea5e9; /* celeste para diferenciarlo del otro */
            color: #ecfeff;
            font-size: 0.8rem;
            text-decoration: none;
            box-shadow: 0 18px 35px rgba(14, 165, 233, 0.7);
        }}

        .whatsapp-publish-fab-icon {{
            width: 1.4rem;
            height: 1.4rem;
            border-radius: 999px;
            background: #0369a1;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            font-size: 1rem;
        }}

        .whatsapp-publish-fab-text {{
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
                width: auto;
                max-width: 90vw;       /* límite para no explotar en pantallas pequeñas */

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
            .whatsapp-publish-fab-text {{
                display: none;
            }}

        }}


            /* ============================
           Sección "Quiénes somos"
        ============================= */
        .about-card {{
            margin: 0.75rem 1.5rem 0.5rem;
            padding: 1.1rem 1.25rem;
            border-radius: var(--border-radius-lg);
            background: radial-gradient(circle at top left, rgba(8, 217, 214, 0.06), transparent 55%),
                        radial-gradient(circle at bottom right, rgba(59, 130, 246, 0.08), transparent 55%),
                        var(--bg-card);
            border: 1px solid rgba(15, 23, 42, 0.9);
            box-shadow: var(--shadow-soft);
            display: grid;
            grid-template-columns: minmax(0, 2.2fr) minmax(0, 1.4fr);
            gap: 1.25rem;
        }}

        @media (max-width: 768px) {{
            .about-card {{
                grid-template-columns: minmax(0, 1fr);
            }}
        }}

        .about-eyebrow {{
            font-size: 0.7rem;
            letter-spacing: 0.16em;
            text-transform: uppercase;
            color: var(--accent);
            font-weight: 600;
            margin-bottom: 0.2rem;
        }}

        .about-title {{
            font-size: 1.25rem;
            font-weight: 600;
            color: var(--text-main);
            margin-bottom: 0.4rem;
        }}

        .about-tagline {{
            font-size: 0.9rem;
            color: var(--text-muted);
            max-width: 32rem;
        }}

        .about-body {{
            margin-top: 0.75rem;
            font-size: 0.85rem;
            color: var(--text-muted);
            line-height: 1.5;
        }}

        .about-highlight {{
            color: var(--accent);
            font-weight: 500;
        }}

        .about-pill-row {{
            display: flex;
            flex-wrap: wrap;
            gap: 0.4rem;
            margin-top: 0.75rem;
        }}

        .about-pill {{
            font-size: 0.7rem;
            padding: 0.25rem 0.55rem;
            border-radius: 999px;
            border: 1px solid rgba(148, 163, 184, 0.4);
            color: var(--text-muted);
            background: rgba(15, 23, 42, 0.9);
        }}

        .about-side-title {{
            font-size: 0.85rem;
            font-weight: 600;
            color: var(--text-main);
            margin-bottom: 0.4rem;
        }}

        .about-list {{
            display: flex;
            flex-direction: column;
            gap: 0.5rem;
            font-size: 0.78rem;
            color: var(--text-muted);
        }}

        .about-list-item {{
            border-left: 2px solid rgba(148, 163, 184, 0.4);
            padding-left: 0.6rem;
        }}

        .about-list-item-title {{
            font-weight: 500;
            color: var(--text-main);
            margin-bottom: 0.1rem;
        }}

        .about-meta {{
            margin-top: 0.7rem;
            font-size: 0.75rem;
            color: var(--text-muted);
        }}

        .about-meta strong {{
            color: var(--text-main);
        }}

        /* Botón "Quiénes somos" en el header */
        .header-link {{
            border: 0;
            background: rgba(15, 23, 42, 0.9);
            color: var(--text-muted);
            font-size: 0.75rem;
            padding: 0.35rem 0.7rem;
            border-radius: 999px;
            cursor: pointer;
            transition: all 0.15s ease;
            display: inline-flex;
            align-items: center;
            gap: 0.25rem;
        }}

        .header-link:hover {{
            color: var(--text-main);
            background: var(--accent-soft);
        }}

        .header-link-dot {{
            width: 0.35rem;
            height: 0.35rem;
            border-radius: 999px;
            background: var(--accent);
        }}

                /* ============================
           Modal "Quiénes somos"
        ============================= */
        .about-modal-backdrop {{
            position: fixed;
            inset: 0;
            background: rgba(15, 23, 42, 0.75);
            backdrop-filter: blur(6px);
            display: flex;
            align-items: center;
            justify-content: center;
            opacity: 0;
            pointer-events: none;
            transition: opacity 0.18s ease-out;
            z-index: 70;
        }}

        .about-modal-backdrop.is-open {{
            opacity: 1;
            pointer-events: auto;
        }}

        .about-modal-content {{
            width: min(720px, 92vw);
            max-height: 80vh;
            border-radius: var(--border-radius-lg);
            background: radial-gradient(circle at top left, rgba(8, 217, 214, 0.06), transparent 55%),
                        radial-gradient(circle at bottom right, rgba(59, 130, 246, 0.08), transparent 55%),
                        var(--bg-card);
            border: 1px solid rgba(15, 23, 42, 0.9);
            box-shadow: var(--shadow-strong);
            padding: 1.25rem 1.5rem 1.35rem;
            position: relative;
            overflow: hidden;
            transform: translateY(12px) scale(0.98);
            opacity: 0;
            transition: opacity 0.18s ease-out,
                        transform 0.18s ease-out;
        }}

        .about-modal-backdrop.is-open .about-modal-content {{
            opacity: 1;
            transform: translateY(0) scale(1);
        }}

        .about-modal-close {{
            position: absolute;
            top: 0.7rem;
            right: 0.8rem;
            border: 0;
            background: transparent;
            color: var(--text-muted);
            font-size: 1.2rem;
            cursor: pointer;
            padding: 0.15rem;
            border-radius: 999px;
            line-height: 1;
            transition: background 0.15s ease, color 0.15s ease, transform 0.1s ease;
        }}

        .about-modal-close:hover {{
            background: rgba(15, 23, 42, 0.9);
            color: var(--text-main);
            transform: scale(1.05);
        }}

        .about-modal-layout {{
            display: grid;
            grid-template-columns: minmax(0, 2.2fr) minmax(0, 1.4fr);
            gap: 1.25rem;
        }}

        @media (max-width: 768px) {{
            .about-modal-layout {{
                grid-template-columns: minmax(0, 1fr);
            }}
        }}

        .about-eyebrow {{
            font-size: 0.7rem;
            letter-spacing: 0.16em;
            text-transform: uppercase;
            color: var(--accent);
            font-weight: 600;
            margin-bottom: 0.2rem;
        }}

        .about-title {{
            font-size: 1.25rem;
            font-weight: 600;
            color: var(--text-main);
            margin-bottom: 0.4rem;
        }}

        .about-tagline {{
            font-size: 0.9rem;
            color: var(--text-muted);
            max-width: 32rem;
        }}

        .about-body {{
            margin-top: 0.75rem;
            font-size: 0.85rem;
            color: var(--text-muted);
            line-height: 1.5;
        }}

        .about-highlight {{
            color: var(--accent);
            font-weight: 500;
        }}

        .about-pill-row {{
            display: flex;
            flex-wrap: wrap;
            gap: 0.4rem;
            margin-top: 0.75rem;
        }}

        .about-pill {{
            font-size: 0.7rem;
            padding: 0.25rem 0.55rem;
            border-radius: 999px;
            border: 1px solid rgba(148, 163, 184, 0.4);
            color: var(--text-muted);
            background: rgba(15, 23, 42, 0.9);
        }}

        .about-side-title {{
            font-size: 0.85rem;
            font-weight: 600;
            color: var(--text-main);
            margin-bottom: 0.4rem;
        }}

        .about-list {{
            display: flex;
            flex-direction: column;
            gap: 0.5rem;
            font-size: 0.78rem;
            color: var(--text-muted);
        }}

        .about-list-item {{
            border-left: 2px solid rgba(148, 163, 184, 0.4);
            padding-left: 0.6rem;
        }}

        .about-list-item-title {{
            font-weight: 500;
            color: var(--text-main);
            margin-bottom: 0.1rem;
        }}

        .about-meta {{
            margin-top: 0.7rem;
            font-size: 0.75rem;
            color: var(--text-muted);
        }}

        .about-meta strong {{
            color: var(--text-main);
        }}

        /* Botón "Quiénes somos" estilo menú (sector derecho) */
        .top-menu-container {{
            display: flex;
            justify-content: flex-end;
            align-items: center;
            gap: 0.45rem;
            margin-bottom: 0.4rem;
        }}

        .top-menu-button {{
            border: 0;
            background: rgba(15, 23, 42, 0.9);
            color: var(--text-muted);
            font-size: 0.78rem;
            padding: 0.3rem 0.7rem;
            border-radius: 999px;
            cursor: pointer;
            transition: all 0.15s ease;
            display: inline-flex;
            align-items: center;
            gap: 0.3rem;
            box-shadow: var(--shadow-soft);
        }}

        .top-menu-button:hover {{
            color: var(--text-main);
            background: var(--accent-soft);
            transform: translateY(-0.5px);
        }}

        .top-menu-button-dot {{
            width: 0.35rem;
            height: 0.35rem;
            border-radius: 999px;
            background: var(--accent);
        }}

    </style>
</head>


<body>
<!-- Modal: Información para vendedores (con IA) -->
<div id="vendor-info-modal" class="hidden">
  <div class="vendor-modal-dialog">
    <div class="vendor-modal-header">
      <div>
        <h2>¿Quieres publicar tus cartas en la vitrina (con IA)?</h2>
        <p class="vendor-modal-subtitle">
          Ahorra tiempo, ordena tu colección y deja que la inteligencia artificial haga la pega fome por ti.
        </p>
      </div>
      <button type="button"
              class="vendor-modal-close"
              onclick="closeVendorInfoModal()">
        ×
      </button>
    </div>

    <div class="vendor-modal-body">
      <!-- Dolor actual -->
      <section>
        <h3>El problema de publicar en Facebook</h3>
        <p>
          Como jugadores de Magic sabemos lo que es una <strong>LATA</strong> publicar cartas:
        </p>
        <ul>
          <li>Sacas fotos en <strong>bloques</strong> (hojas, pilas, la mesa llena).</li>
          <li>Subes las fotos al grupo y escribes el típico “CK 750” o “2 lucas cada una”.</li>
          <li>Tienes que editar el post cuando se venden cartas, volver a publicar, repetir todo.</li>
        </ul>
        <p>
          Terminas gastando más tiempo en <strong>postear</strong> que en jugar o negociar.
        </p>
      </section>

      <!-- Cómo funciona la vitrina -->
      <section>
        <h3>Cómo funciona la vitrina de Magic Concepción (potenciada con IA)</h3>
        <ol>
          <li>
            Tomas fotos de tus cartas <strong>una por una</strong> (una carta por foto) y las subes
            a una carpeta que te indicaremos.
          </li>
          <li>
            La plataforma usa un <strong>modelo de inteligencia artificial</strong> entrenado para leer las cartas:
            nombre, edición, idioma etc.
          </li>
          <li>
            Con eso se genera un catálogo ordenado, <strong>buscable y filtrable</strong> por nombre,
            edición, idioma, foil, formato, etc.
          </li>
          <li>
            Cada carta queda con un botón de <strong>WhatsApp directo hacia ti</strong>,
            para que negocies sin intermediarios ni comisiones.
          </li>
        </ol>
        <p>
          Además, el sitio completo se publica regularmente en el grupo <strong>Magic Concepción</strong>
          como aporte a la comunidad, para que todos tengamos una vitrina decente sin sufrir con los posts.
        </p>
      </section>

      <!-- Beneficios -->
      <section>
        <h3>Qué ganas tú</h3>
        <ul>
          <li><strong>Tiempo</strong>: la IA hace el trabajo fome de ordenar y etiquetar.</li>
          <li><strong>Tranquilidad</strong>: tu stock está en una vitrina 24/7, no perdido en el feed.</li>
          <li><strong>Visibilidad</strong>: todo el sitio se comparte en el grupo, no solo tu post.</li>
        </ul>
      </section>

      <!-- Planes -->
      <section>
        <h3>Planes para vendedores</h3>
        <div class="plan-card">
        <div class="plan-title">
            <span class="plan-icon">🚀</span>
            Plan Starter
        </div>
        <div class="plan-desc">
            <ul>
              <li>Hasta <strong>300 imágenes</strong> procesadas por IA.</li>
              <li>Ideal para vender carpeta o lote chico.</li>
              <li>Te olvidas de rehacer posts todas las semanas.</li>
            </ul>
        </div>
        <div class="plan-price">$2990 / mes</div>
        </div>

        <div class="plan-card">
            <div class="plan-title">
                <span class="plan-icon">🔥</span>
                Plan Trader
            </div>
            <div class="plan-desc">
                <ul>
                <li>Hasta <strong>1.500 imágenes</strong> procesadas por IA.</li>
                <li>Perfecto si vendes cartas todas las semanas.</li>
                <li>Rotas stock sin tener que rearmar publicaciones.</li>
                </ul>
            </div>
            <div class="plan-price">$5.990 CLP / mes</div>
        </div>

        <div class="plan-card">
            <div class="plan-title">
                <span class="plan-icon">💎</span>
                Plan Profesional
            </div>
            <div class="plan-desc">
            <ul>
              <li>Hasta <strong>3.000 imágenes</strong> procesadas por IA.</li>
              <li>Para coleccionistas grandes o vendedores fuertes.</li>
              <li>Soporte prioritario y ayuda para optimizar tu catálogo.</li>
            </ul>    
            </div>
            <div class="plan-price">$9.990 CLP / mes</div>
        </div>


        <div class="vendor-launch-note">
          <p><strong>🚀 Lanzamiento para la comunidad de Magic</strong></p>
          <p>
            Los <strong>primeros 10 vendedores</strong> tienen el plan completo
            <strong>GRATIS por 3 meses</strong>, hasta 300 imágenes procesadas con IA,
            mientras afinamos la plataforma con su feedback.
          </p>
        </div>
      </section>

      <!-- Call to action -->
      <section>
        <h3>¿Te interesa publicar tus cartas con IA?</h3>
        <p>
          Escríbeme por WhatsApp y armamos tu carpeta  para subir las fotos de tus cartas. 
          Tú subes las fotos, la inteligencia artificial se encarga del resto.
        </p>
        <div class="vendor-modal-actions">
          <a href="https://wa.me/56990590045?text=Hola%2C%20quiero%20publicar%20mis%20cartas%20en%20la%20vitrina%20de%20Magic%20Concepci%C3%B3n%20con%20IA.%20%C2%BFC%C3%B3mo%20parto%3F"
             target="_blank"
             rel="noopener"
             class="vendor-modal-primary">
            Hablar por WhatsApp
          </a>
          <button type="button"
                  class="vendor-modal-secondary"
                  onclick="closeVendorInfoModal()">
            Más tarde, solo quiero seguir viendo cartas
          </button>
        </div>
      </section>
    </div>
  </div>
</div>


<div class="page-shell">
    <header>
        <div class="header-inner">
            <div class="brand">
                <div class="brand-icon">M</div>
                <div class="brand-text-wrapper">
                    <div class="brand-text-main">La Comunidad del Magic</div>
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
            <div class="top-menu-container">
                <button
                    type="button"
                    class="top-menu-button"
                    onclick="openAboutModal()"
                >
                    <span class="top-menu-button-dot"></span>
                    <span>Quiénes somos</span>
                </button>
            </div>
            <div class="search-header">
                <div>
                    <div class="search-title">Busca tu carta</div>
                    <div class="search-sub">Puedes buscar por nombre, set o idioma.</div>
                </div>
            </div>
            <div class="search-input-wrapper">
                <span class="search-icon">🔍</span>
                <input
                    id="searchInput"
                    class="search-input"
                    type="text"
                    placeholder="Ej: Lightning Bolt, MH2, Español..."
                    autocomplete="off"
                />
            </div>
            <div class="search-hint">
                <strong>Tip:</strong> escribe parte del nombre o el código de la edicion, hay cartas en español y en ingles.
                Los precios son referenciales.
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

    <!-- Botón flotante de WhatsApp general (tú) -->
    <a
        href="https://wa.me/56990590045?text=Hola%20me%20interesa%20una%20carta%20de%20tu%20tienda"
        class="whatsapp-fab"
        target="_blank"
        rel="noopener noreferrer"
    >
        <span class="whatsapp-fab-icon">💬</span>
        <span class="whatsapp-fab-text">¿Te interesa saber mas? Háblame por WhatsApp</span>
    </a>

      <!-- Botón flotante para que otros publiquen sus cartas (abre modal con IA) -->
        <button
            type="button"
            class="whatsapp-publish-fab"
            onclick="openVendorInfoModal()"
        >
            <span class="whatsapp-publish-fab-icon">📸</span>
            <span class="whatsapp-publish-fab-text">
                Publicar mis cartas con IA
            </span>
        </button>


    <footer>
        <div class="footer-inner">
            <span>Sitio construido y especializado para trabajar con IA.</span>
            <span>La Comunidad del Magic &copy; 2025</span>
        </div>
    </footer>
</div>

<script>
    const IMAGE_BASE_PATH = "{IMAGES_BASE_URL}";
    const PAGE_SIZE = 30;
    const cardsData = {cards_json};
    const IS_COARSE_POINTER = window.matchMedia && window.matchMedia("(pointer: coarse)").matches;

    let filteredCards = [...cardsData];
    let currentPage = 1;

    const $ = (id) => document.getElementById(id);

        function openAboutModal() {{
        const backdrop = document.getElementById("aboutModalBackdrop");
        if (!backdrop) return;
        backdrop.classList.add("is-open");
    }}

    function closeAboutModal() {{
        const backdrop = document.getElementById("aboutModalBackdrop");
        if (!backdrop) return;
        backdrop.classList.remove("is-open");
    }}

    // Cerrar modal al hacer clic en el fondo
    document.addEventListener("click", (ev) => {{
        const backdrop = document.getElementById("aboutModalBackdrop");
        if (!backdrop || !backdrop.classList.contains("is-open")) return;

        if (ev.target === backdrop) {{
            closeAboutModal();
        }}
    }});

    // Cerrar modal con Escape
    document.addEventListener("keydown", (ev) => {{
        if (ev.key === "Escape") {{
            closeAboutModal();
        }}
    }});


        function scrollToAbout() {{
        const el = document.getElementById("aboutSection");
        if (!el) return;
        el.scrollIntoView({{ behavior: "smooth", block: "start" }});
    }}


    function debounce(fn, delay) {{
        let timer = null;
        return (...args) => {{
            if (timer) clearTimeout(timer);
            timer = setTimeout(() => fn(...args), delay);
        }};
    }}

    function formatClpJs(value) {{
        if (value == null || value === "" || isNaN(value)) {{
            return "Consultar";
        }}
        try {{
            return new Intl.NumberFormat("es-CL", {{
                style: "currency",
                currency: "CLP",
                minimumFractionDigits: 0,
                maximumFractionDigits: 0
            }}).format(value);
        }} catch (e) {{
            return "$ " + value;
        }}
    }}

            function openCopiesModal(card) {{
        const modal = document.getElementById("copiesModal");
        const titleEl = document.getElementById("copiesModalTitle");
        const metaEl = document.getElementById("copiesModalMeta");
        const bodyEl = document.getElementById("copiesModalBody");
        const dialog = document.querySelector(".copies-modal-dialog");

        if (!modal || !titleEl || !metaEl || !bodyEl || !dialog) return;

        // --- Título ---
        titleEl.textContent = card.name || "";
        metaEl.textContent = "";

        // --- Limpiar contenido anterior ---
        bodyEl.innerHTML = "";

        const grid = document.createElement("div");
        grid.className = "copies-modal-grid";

        // Si hay copias, las usamos; si no, usamos la imagen principal
        const copies = (Array.isArray(card.copies) && card.copies.length)
            ? card.copies
            : [{{ imageFile: card.imageFile }}];

        const count = copies.length;

        // Marcar si es una sola copia para que el CSS ajuste ancho y columnas
        if (count === 1) {{
            dialog.classList.add("single-copy");
        }} else {{
            dialog.classList.remove("single-copy");
        }}

        for (const copy of copies) {{
            const item = document.createElement("div");
            item.className = "copies-modal-item";

            const imgWrap = document.createElement("div");
            imgWrap.className = "copies-modal-imgwrap";

            const img = document.createElement("img");
            img.loading = "lazy";
            img.alt = (card.name || "") + " - copia";
            img.src = IMAGE_BASE_PATH + "/" + encodeURI(copy.imageFile || card.imageFile || "");

            imgWrap.appendChild(img);
            item.appendChild(imgWrap);

            // si quieres agregar info por copia (idioma, qty, whatsapp) se mantiene debajo
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
        setTag.textContent = card.set || "Set desconocido";
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

        // ----- Footer en 3 filas: precio / Ver stock / WhatsApp
        const footer = document.createElement("div");
        footer.className = "card-footer";

        // Fila 1: precio (CK 750 o $XXX)
        const priceBox = document.createElement("div");
        const priceMain = document.createElement("div");
        priceMain.className = "price-main";
        priceMain.textContent = card.price;   // ya viene con "CK 750" si aplica
        priceBox.appendChild(priceMain);

        if (card.priceUsdRef) {{
            const priceRef = document.createElement("div");
            priceRef.className = "price-ref";
            priceBox.appendChild(priceRef);
        }}
        footer.appendChild(priceBox);

        // Fila 2: botón Ver stock
        const qtyButton = document.createElement("button");
        qtyButton.type = "button";
        qtyButton.className = "qty-pill";

        // usamos la cantidad de copias reales si existe, si no, caemos a quantity
        const copiesCount = (Array.isArray(card.copies) && card.copies.length > 0)
            ? card.copies.length
            : (card.quantity || 1);

        qtyButton.textContent = "Ver stock (" + copiesCount + ")";

        qtyButton.addEventListener("click", (event) => {{
            event.stopPropagation();
            openCopiesModal(card);
        }});
        footer.appendChild(qtyButton);


        // Fila 3: botón WhatsApp (solo si hay teléfono)
        if (card.seller_phone) {{
            const waBtn = document.createElement("a");
            waBtn.className = "qty-pill";
            waBtn.style.background = "#22c55e";
            waBtn.style.color = "white";
            waBtn.style.border = "none";
            waBtn.style.textDecoration = "none";
            waBtn.style.display = "inline-flex";
            waBtn.style.alignItems = "center";
            waBtn.style.gap = "0.25rem";

            const msg = encodeURIComponent(
                "Hola, vi tu carta " + card.name + " en la tienda y me interesa."
            );
            waBtn.href = "https://wa.me/" + card.seller_phone + "?text=" + msg;
            waBtn.target = "_blank";

            const icon = document.createElement("span");
            icon.textContent = "💬";
            waBtn.appendChild(icon);

            const txt = document.createElement("span");
            txt.textContent = "Hablar por WhatsApp";
            waBtn.appendChild(txt);

            footer.appendChild(waBtn);
        }}

        body.appendChild(footer);




        article.appendChild(imageWrapper);
        article.appendChild(body);

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
                    card.condition || "",
                    card.searchAliases || ""   
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
    // --- Modal "¿Quieres publicar tus cartas?" ---

    // --- Modal "¿Quieres publicar tus cartas?" ---

    function openVendorInfoModal() {{
        const modal = document.getElementById("vendor-info-modal");
        if (!modal) return;

        // Primero nos aseguramos de que no esté display:none
        modal.classList.remove("hidden");

        // Forzamos un reflow para que el navegador "registre" el estado inicial
        // antes de aplicar la clase .flex (esto hace que la transición se vea bien)
        void modal.offsetWidth;

        // Ahora sí, mostramos y activamos las transiciones (opacity + transform)
        modal.classList.add("flex");
    }}

    function closeVendorInfoModal() {{
    const modal = document.getElementById("vendor-info-modal");
    const dialog = document.querySelector(".vendor-modal-dialog");

    // Animación de salida
    dialog.style.animation = "vendorSlideDown 0.28s ease-in forwards";

    // Esperar la animación antes de ocultar
    setTimeout(() => {{
        modal.classList.remove("flex");
        modal.classList.add("hidden");

        // Restablecer animación de entrada para la próxima vez
        dialog.style.animation = "vendorSlideUpBounce 0.45s cubic-bezier(.22,1.28,.57,1) forwards";
    }}, 280);
}}



    // Cerrar al clickear fuera del contenido
    document.addEventListener("click", function (e) {{
    const modal = document.getElementById("vendor-info-modal");
    if (!modal) return;
    if (!modal.classList.contains("flex")) return;

    // si el click fue directamente sobre el backdrop (no sobre el contenido)
    if (e.target === modal) {{
        closeVendorInfoModal();
    }}
    }});

    // Cerrar con ESC
    document.addEventListener("keydown", function (e) {{
    if (e.key === "Escape") {{
        closeVendorInfoModal();
    }}
    }});


    document.addEventListener("DOMContentLoaded", init);
</script>
        <div id="aboutModalBackdrop" class="about-modal-backdrop">
            <div class="about-modal-content">
                <button
                    type="button"
                    class="about-modal-close"
                    onclick="closeAboutModal()"
                    aria-label="Cerrar"
                >
                    &times;
                </button>

                <div class="about-modal-layout">
                    <div>
                <div class="about-eyebrow">Quiénes somos</div>
                <div class="about-title">Un marketplace de Magic creado por y para jugadores</div>
                <p class="about-tagline">
                    La Comunidad del Magic es una plataforma chilena dedicada a la compra y venta de cartas individuales
                    de <strong>Magic: The Gathering</strong>, creada por jugadores para jugadores.
                    Nuestro objetivo es hacer que encontrar y vender cartas sea simple, transparente y confiable.
                </p>
                <p class="about-body">
                    Nacimos desde una necesidad real de la comunidad:
                    <span class="about-highlight">acceder a cartas originales con precios justos y fotos reales</span>,
                    sin intermediarios innecesarios ni pérdida de tiempo.
                    Aquí cada carta publicada muestra su estado real, su edición y un contacto directo con la persona que la vende.
                    Utilizamos herramientas de inteligencia artificial para mantener el catálogo ordenado y actualizado,
                    pero la base del proyecto sigue siendo la confianza entre jugadores.
                </p>
                <div class="about-pill-row">
                    <div class="about-pill">Cartas individuales con fotos reales</div>
                    <div class="about-pill">Vendedores reales, contacto directo</div>
                    <div class="about-pill">Precios referenciados al mercado internacional</div>
                    <div class="about-pill">Comunidad Magic activa en Chile</div>
                </div>
            </div>


                    <div>
                        <div class="about-side-title">Lo que queremos lograr</div>
                        <div class="about-list">
                            <div class="about-list-item">
                                <div class="about-list-item-title">Ayudar a quienes compran</div>
                                <div>
                                    Que puedas armar tus mazos con información transparente: estado real de la carta,
                                    edición correcta, idioma y precio claro. Si te interesa algo, un clic en WhatsApp
                                    te conecta directo con el dueño.
                                </div>
                            </div>
                            <div class="about-list-item">
                                <div class="about-list-item-title">Ayudar a quienes venden</div>
                                <div>
                                    Sabemos lo tedioso que es catalogar cientos de cartas. Queremos que puedas sacar fotos,
                                    enviarlas y que la vitrina se arme por ti, usando IA para etiquetar y publicar tu stock
                                    con el menor esfuerzo posible.
                                </div>
                            </div>
                            <div class="about-list-item">
                                <div class="about-list-item-title">Cuidar la comunidad</div>
                                <div>
                                    Nos importa que el trato sea cercano, respetuoso y justo.
                                    Este sitio existe para que más jugadores puedan disfrutar el juego,
                                    rotar su colección y darle nueva vida a cartas que hoy están guardadas en una caja.
                                </div>
                            </div>
                        </div>
                        <div class="about-meta">
                            <strong>Visión:</strong> ser la vitrina de referencia para cartas individuales en Chile,
                            donde la tecnología se pone al servicio de la comunidad y no al revés.
                        </div>
                    </div>
                </div>
            </div>
        </div>

</body>
</html>
"""
    return template


# ========== COPIA DE IMÁGENES ==========

def copy_images():
    """Copia todas las imágenes desde PROCESADAS_DIR al repo de imágenes (IMAGES_REPO_DIR)."""
    if not PROCESADAS_DIR.exists():
        raise SystemExit(f"[ERROR] No existe PROCESADAS_DIR: {PROCESADAS_DIR}")

    IMAGES_REPO_DIR.mkdir(parents=True, exist_ok=True)

    # Borrar imágenes anteriores del repo de imágenes
    for f in IMAGES_REPO_DIR.iterdir():
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
            # Aplanamos la estructura: todas las imágenes quedan en /images con su nombre
            dst = IMAGES_REPO_DIR / src.name
            shutil.copy2(src, dst)
            count += 1

    print(f"[INFO] Se copiaron {count} imágenes a {IMAGES_REPO_DIR}")




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
    rows = load_inventory(SELLER_INVENTORIES_DIR)


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
    #git_commit_and_push()

    print("\n[OK] Flujo completo terminado.\n")


if __name__ == "__main__":
    logger = get_logger("actualizar_tienda")
    log_info("==== INICIO actualizar_tienda ====", logger)
    try:
        main()
        log_info("==== FIN OK actualizar_tienda ====", logger)
    except Exception as e:
        log_exception(e, logger, "actualizar_tienda terminó con ERROR")
        raise

