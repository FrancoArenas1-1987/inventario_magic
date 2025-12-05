import csv
import gzip
import json
import os
import sys
import unicodedata
from pathlib import Path
from typing import Dict, Any, Tuple

import requests
from dotenv import load_dotenv

from config_tienda import PROJECT_ROOT, INVENTORY_CSV

# ============================================================
# CONFIGURACIÓN DESDE .env
# ============================================================

# Cargamos el .env desde la carpeta del proyecto
load_dotenv(PROJECT_ROOT / ".env")

def _get_float_env(name: str, default: float) -> float:
    val = os.getenv(name)
    if val is None or val == "":
        return float(default)
    try:
        return float(val)
    except ValueError:
        return float(default)

USD_TO_CLP = _get_float_env("USD_TO_CLP", 950.0)
SCRYFALL_USD_TO_CLP = _get_float_env("SCRYFALL_USD_TO_CLP", USD_TO_CLP)

# GLOBAL_DISCOUNT_PERCENT es un porcentaje (ej: 10 => 10%)
GLOBAL_DISCOUNT_PERCENT = _get_float_env("GLOBAL_DISCOUNT_PERCENT", 0.0)
GLOBAL_DISCOUNT = GLOBAL_DISCOUNT_PERCENT / 100.0

# Piso mínimo en CLP
PRICE_MIN_CLP = _get_float_env("PRICE_MIN_CLP", 500.0)

# Orden de preferencia de proveedores de MTGJSON
PREFERRED_PROVIDERS = ["cardkingdom", "tcgplayer", "cardmarket", "cardsphere"]

# Multiplicadores por condición
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
    """Similitud simple por palabras (rápida). Se deja por compatibilidad."""
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
    - es_to_en[es_norm] = en_raw (nombre inglés base)
    - en_to_es[en_norm] = es_raw (primer nombre español encontrado)
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

        # Índice por (set, nombre_en_normalizado)
        card_index[(set_code, en_norm)] = uuid

        # Traducciones ES↔EN
        for fd in card.get("foreignData", []) or []:
            if fd.get("language") == "Spanish":
                es_raw = fd.get("name") or ""
                es_norm = normalize(es_raw)
                if es_norm:
                    # No es set-específico, pero luego lo validamos contra el índice de set
                    es_to_en[es_norm] = en_raw
                    # Un mapeo simple inverso (no usado críticamente, pero se mantiene)
                    en_to_es[en_norm] = es_raw

    print(f"[OK] Traducciones ES→EN: {len(es_to_en)} | Cartas indexadas: {len(card_index)}")
    return es_to_en, en_to_es, card_index


import unicodedata

def _normalize_name_for_lookup(s: str) -> str:
    """
    Normaliza un nombre para compararlo:
    - quita tildes
    - pasa a minúsculas
    - recorta espacios
    """
    if not s:
        return ""
    s = s.strip()
    # quitar acentos
    s = "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )
    return s.lower()


def resolve_name_to_english(name: str, es_to_en: dict) -> str:
    """
    Devuelve el nombre en inglés usando el mapa es_to_en.
    Si no encuentra el nombre, devuelve el mismo name sin romper.
    """
    if not name:
        return ""

    # 1) intento directo
    if name in es_to_en:
        return es_to_en[name]

    # 2) intento con normalización (sin tildes, lower)
    norm_name = _normalize_name_for_lookup(name)

    # construimos un dict normalizado por si las claves vienen con tildes
    for es_name, en_name in es_to_en.items():
        if _normalize_name_for_lookup(es_name) == norm_name:
            return en_name

    # 3) fallback: devolvemos el mismo nombre (no rompemos el flujo)
    return name



# ============================================================
# PRECIOS DESDE MTGJSON (POR uuid)
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
# PRECIOS DESDE SCRYFALL (FALLBACK EXCLUSIVO POR set+name)
# ============================================================

def get_price_from_scryfall(name_en: str, set_code: str, is_foil: bool, condition: str):
    """
    Obtiene precio EXCLUSIVAMENTE de la impresión REAL (set_code) en Scryfall.

    - Si no existe una carta con ese nombre EXACTO y ese set_code, no se devuelve precio.
    - NO hace fallback a otras ediciones: si no hay datos para ese set, retorna None.
    """
    set_code = (set_code or "").strip().lower()
    name_en = (name_en or "").strip()
    if not name_en or not set_code:
        return None

    try:
        resp = requests.get(
            "https://api.scryfall.com/cards/named",
            params={"exact": name_en, "set": set_code},
            timeout=12,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
    except Exception:
        return None

    prices = data.get("prices") or {}
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
        # Si el precio fue marcado como manual, no lo tocamos (por si lo usas).
        if str(row.get("price_source", "")).lower() == "manual":
            continue

        name = (row.get("name", "") or "").strip()
        set_code = (row.get("set", "") or "").strip().upper()
        lang = (row.get("lang", "") or "").lower()
        condition = row.get("condition", "NM")
        is_foil_str = str(row.get("is_foil", "")).lower()
        is_foil = is_foil_str in ("1", "true", "yes", "y", "foil")

        # Si no hay nombre, no podemos hacer nada
        if not name:
            row["price_usd_ref"] = ""
            row["price_clp"] = ""
            row["price_source"] = ""
            without_price += 1
            continue

        # 🔒 REGLA QUE PEDISTE:
        # Si la carta NO tiene edición (set vacío), NO buscamos precio.
        # Esto ocurre cuando visión no pudo detectar el set_code.
        if not set_code:
            row["price_usd_ref"] = ""
            row["price_clp"] = ""
            row["price_source"] = ""
            without_price += 1
            continue

        # 1) Resolver nombre en inglés si la carta está en español
        if lang == "es":
            name_en_raw = resolve_name_to_english(name, es_to_en)
        else:
            name_en_raw = name

        if not name_en_raw:
            row["price_usd_ref"] = ""
            row["price_clp"] = ""
            row["price_source"] = ""
            without_price += 1
            continue

        name_en_norm = normalize(name_en_raw)

        # 2) Buscar uuid de la impresión exacta por (set_code, name_en_norm)
        uuid = card_index.get((set_code, name_en_norm))

        price_usd = None
        price_clp = None
        source = ""

        # 2.a) Intentar MTGJSON
        if uuid:
            price_entry = prices_data.get(uuid)
            if price_entry:
                mtg_result = get_price_from_mtgjson(price_entry, is_foil, condition)
                if mtg_result:
                    price_usd, price_clp, source = mtg_result

        # 3) Fallback: Scryfall SOLO para ese set+nombre
        if price_usd is None:
            scry_result = get_price_from_scryfall(name_en_raw, set_code, is_foil, condition)
            if scry_result:
                price_usd, price_clp, source = scry_result

        # 4) Escribir resultado en el CSV
        if price_usd is None:
            # No encontramos precio válido para esa edición → Consultar
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
    # force_download=True para asegurarse de tener MTGJSON fresco
    actualizar_inventario(force_download=True)
