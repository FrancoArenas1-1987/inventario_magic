import csv
import gzip
import json
import os
import sys
import unicodedata
from pathlib import Path
from typing import Dict, Any, Tuple

import requests

from config_tienda import PROJECT_ROOT, INVENTORY_CSV
from pathlib import Path
from dotenv import load_dotenv

from config_tienda import PROJECT_ROOT, INVENTORY_CSV

# Cargar .env desde la carpeta del proyecto
load_dotenv(PROJECT_ROOT / ".env")

USD_TO_CLP = float(os.getenv("USD_TO_CLP", 950))
SCRYFALL_USD_TO_CLP = float(os.getenv("SCRYFALL_USD_TO_CLP", 900))


# ============================================================
# CONFIGURACIÓN DESDE .env
# ============================================================

USD_TO_CLP = float(os.getenv("USD_TO_CLP", 900))
SCRYFALL_USD_TO_CLP = float(os.getenv("SCRYFALL_USD_TO_CLP", 900))
GLOBAL_DISCOUNT = float(os.getenv("GLOBAL_DISCOUNT_PERCENT", 0.00))

PRICE_MIN_CLP = 500

PREFERRED_PROVIDERS = ["cardkingdom", "tcgplayer", "cardmarket", "cardsphere"]

CONDITION_MULTIPLIERS = {
    "NM": 1.00, "M": 1.00,
    "EX": 0.90, "SP": 0.90,
    "VG": 0.80, "MP": 0.80,
    "HP": 0.60, "POOR": 0.40
}

MTGJSON_DIR = PROJECT_ROOT / "mtgjson"
ALL_IDENTIFIERS_GZ = MTGJSON_DIR / "AllIdentifiers.json.gz"
ALL_PRICES_TODAY_GZ = MTGJSON_DIR / "AllPricesToday.json.gz"

ALL_IDENTIFIERS_URL = "https://mtgjson.com/api/v5/AllIdentifiers.json.gz"
ALL_PRICES_TODAY_URL = "https://mtgjson.com/api/v5/AllPricesToday.json.gz"


# ============================================================
# UTILIDADES
# ============================================================

def normalize(s: str) -> str:
    """Normaliza string (sin acentos, lowercase, trim)."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.lower().strip()


def similarity(a: str, b: str) -> float:
    """Similitud simple por palabras (rápida)."""
    a = normalize(a)
    b = normalize(b)
    if not a or not b:
        return 0.0
    set_a = set(a.split())
    set_b = set(b.split())
    if not set_a or not set_b:
        return 0.0
    overlap = len(set_a & set_b)
    return overlap / max(len(set_a), len(set_b))


def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"[INFO] Descargando {url} ...")
    r = requests.get(url, stream=True, timeout=60)
    r.raise_for_status()
    with open(dest, "wb") as f:
        for chunk in r.iter_content(8192):
            if chunk:
                f.write(chunk)
    print(f"[OK] Guardado en {dest}")


def ensure_mtgjson_files(force: bool = False):
    if force or not ALL_IDENTIFIERS_GZ.exists():
        download_file(ALL_IDENTIFIERS_URL, ALL_IDENTIFIERS_GZ)
    if force or not ALL_PRICES_TODAY_GZ.exists():
        download_file(ALL_PRICES_TODAY_URL, ALL_PRICES_TODAY_GZ)


def load_json_gz(path: Path) -> Dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


# ============================================================
# DICCIONARIOS ES↔EN + ÍNDICE (set+name_en → uuid)
# ============================================================

def build_translation_maps_and_index(identifiers: Dict[str, Any]):
    """
    - es_to_en[es_norm] = en_raw
    - en_to_es[en_norm] = es_raw
    - card_index[(set_code, en_norm)] = uuid
    """
    print("[INFO] Construyendo diccionarios ES↔EN e índice de cartas...")
    es_to_en: Dict[str, str] = {}
    en_to_es: Dict[str, str] = {}
    card_index: Dict[Tuple[str, str], str] = {}

    data = identifiers.get("data", {})
    for uuid, card in data.items():
        en_raw = card.get("name") or ""
        en_norm = normalize(en_raw)
        set_code = (card.get("setCode") or "").upper()
        if not set_code or not en_norm:
            continue

        card_index[(set_code, en_norm)] = uuid

        for fd in card.get("foreignData", []) or []:
            if fd.get("language") == "Spanish":
                es_raw = fd.get("name") or ""
                es_norm = normalize(es_raw)
                if es_norm:
                    es_to_en[es_norm] = en_raw
                    en_to_es[en_norm] = es_raw

    print(f"[OK] Traducciones ES→EN: {len(es_to_en)} | Cartas indexadas: {len(card_index)}")
    return es_to_en, en_to_es, card_index


def resolve_name_to_english(name_es: str, es_to_en: Dict[str, str]) -> str:
    """Devuelve nombre EN crudo a partir de un nombre ES (directo o por similitud)."""
    name_es_norm = normalize(name_es)
    if not name_es_norm:
        return name_es

    # Match exacto
    if name_es_norm in es_to_en:
        return es_to_en[name_es_norm]

    # Match aproximado
    best_key = None
    best_score = 0.0
    for es_norm in es_to_en.keys():
        sc = similarity(name_es_norm, es_norm)
        if sc > best_score:
            best_score = sc
            best_key = es_norm

    if best_key is not None and best_score > 0.33:
        return es_to_en[best_key]

    # Último recurso: devolver el nombre español tal cual
    return name_es


# ============================================================
# PRECIOS DESDE MTGJSON
# ============================================================

def get_price_from_mtgjson(price_entry: Dict[str, Any], is_foil: bool, condition: str):
    paper = price_entry.get("paper", {})
    provider_name = None
    provider_data = None

    for prov in PREFERRED_PROVIDERS:
        if prov in paper:
            provider_name = prov
            provider_data = paper[prov]
            break

    if not provider_data:
        return None

    retail = provider_data.get("retail", {})
    finish_key = "foil" if is_foil else "normal"
    prices_dict = retail.get(finish_key) or retail.get("foil" if finish_key == "normal" else "normal")
    if not prices_dict:
        return None

    try:
        last_date = sorted(prices_dict.keys())[-1]
        base_usd = float(prices_dict[last_date])
    except Exception:
        return None

    cond_mult = CONDITION_MULTIPLIERS.get(condition.upper(), 1.0)
    adj_usd = base_usd * cond_mult
    adj_clp = adj_usd * USD_TO_CLP

    # Descuento global
    adj_clp *= (1 - GLOBAL_DISCOUNT)

    # Piso mínimo
    if adj_clp < PRICE_MIN_CLP:
        adj_clp = PRICE_MIN_CLP

    return adj_usd, adj_clp, provider_name


# ============================================================
# PRECIOS DESDE SCRYFALL (FALLBACK)
# ============================================================

def get_price_from_scryfall(name_en: str, set_code: str, is_foil: bool, condition: str):
    """
    Obtiene precio exclusivamente de la impresión REAL (set_code) en Scryfall.

    - Si no existe una carta con ese nombre EXACTO y ese set_code, no se devuelve precio.
    - NO hace fallback a otras ediciones: si no hay datos para ese set, retorna None.
    """
    set_code = (set_code or "").strip().lower()
    name_en = (name_en or "").strip()
    if not name_en or not set_code:
        return None

    try:
        # Solo buscamos la carta de ese set específico
        resp = requests.get(
            "https://api.scryfall.com/cards/named",
            params={"exact": name_en, "set": set_code},
            timeout=12,
        )
        if resp.status_code != 200:
            # No hay carta para ese nombre+set -> sin precio
            return None
        data = resp.json()
    except Exception:
        return None

    prices = data.get("prices") or {}

    # Elegimos el campo base dependiendo de foil / no foil
    if is_foil:
        base_str = prices.get("usd_foil") or prices.get("usd")
    else:
        base_str = prices.get("usd") or prices.get("usd_foil")

    if not base_str:
        return None

    try:
        base_usd = float(base_str)
    except (TypeError, ValueError):
        return None

    cond_mult = CONDITION_MULTIPLIERS.get(condition.upper(), 1.0)
    adj_usd = base_usd * cond_mult
    adj_clp = adj_usd * SCRYFALL_USD_TO_CLP

    # Aplicar descuento global y piso mínimo, igual que MTGJSON
    adj_clp *= (1 - GLOBAL_DISCOUNT)
    if adj_clp < PRICE_MIN_CLP:
        adj_clp = PRICE_MIN_CLP

    return adj_usd, adj_clp, "scryfall"



# ============================================================
# ACTUALIZAR INVENTARIO COMPLETO
# ============================================================

def actualizar_inventario(force_download: bool = False):
    print("[INFO] Preparando archivos MTGJSON...")
    ensure_mtgjson_files(force_download)

    identifiers = load_json_gz(ALL_IDENTIFIERS_GZ)
    prices_data = load_json_gz(ALL_PRICES_TODAY_GZ).get("data", {})

    es_to_en, en_to_es, card_index = build_translation_maps_and_index(identifiers)

    print(f"[INFO] Leyendo inventario: {INVENTORY_CSV}")
    if not INVENTORY_CSV.exists():
        print(f"[ERROR] No se encontró el archivo de inventario: {INVENTORY_CSV}")
        sys.exit(1)

    with open(INVENTORY_CSV, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        headers = reader.fieldnames or []

    if "price_source" not in headers:
        headers.append("price_source")

    updated = 0
    without_price = 0

    print("[INFO] Actualizando precios de cartas...")
    for row in rows:
        name = row.get("name", "")
        set_code = (row.get("set", "") or "").upper()
        lang = (row.get("lang", "") or "").lower()
        condition = row.get("condition", "NM")
        is_foil_str = str(row.get("is_foil", "")).lower()
        is_foil = is_foil_str in ("1", "true", "yes", "y", "foil")

        if not name or not set_code:
            row["price_usd_ref"] = ""
            row["price_clp"] = ""
            row["price_source"] = ""
            continue

        # 1) Resolver nombre en inglés si la carta está en español
        if lang == "es":
            name_en_raw = resolve_name_to_english(name, es_to_en)
        else:
            name_en_raw = name

        name_en_norm = normalize(name_en_raw)
        uuid = card_index.get((set_code, name_en_norm))

        price_usd = None
        price_clp = None
        source = ""

        # 2) Intentar MTGJSON
        if uuid:
            price_entry = prices_data.get(uuid)
            if price_entry:
                mtg_result = get_price_from_mtgjson(price_entry, is_foil, condition)
                if mtg_result:
                    price_usd, price_clp, source = mtg_result

        # 3) Fallback: Scryfall
        if price_usd is None:
            scry_result = get_price_from_scryfall(name_en_raw, set_code, is_foil, condition)
            if scry_result:
                price_usd, price_clp, source = scry_result

        # 4) Escribir resultado en el CSV
        if price_usd is None:
            row["price_usd_ref"] = ""
            row["price_clp"] = ""
            row["price_source"] = ""
            without_price += 1
        else:
            row["price_usd_ref"] = f"{price_usd:.2f}"
            row["price_clp"] = str(int(round(price_clp)))
            row["price_source"] = source
            updated += 1

    tmp_path = INVENTORY_CSV.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)

    INVENTORY_CSV.unlink(missing_ok=True)
    tmp_path.replace(INVENTORY_CSV)

    print("[OK] Precios actualizados.")
    print(f"  Cartas con precio: {updated}")
    print(f"  Cartas sin precio: {without_price}")


if __name__ == "__main__":
    actualizar_inventario(force_download=True)
