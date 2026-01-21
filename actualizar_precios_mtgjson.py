import csv
import gzip
import json
import os
import sys
import unicodedata
from pathlib import Path
from typing import Dict, Any, Tuple
from logger_tienda import get_logger, log_info, log_exception

import requests
from dotenv import load_dotenv

from config_tienda import PROJECT_ROOT, INVENTORY_CSV

# Directorio de inventarios por vendedor
SELLER_INVENTORIES_DIR: Path = INVENTORY_CSV.parent / "inventarios_vendedores"

# ============================================================
# CONFIGURACIÓN DESDE .env
# ============================================================

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
GLOBAL_DISCOUNT_PERCENT = _get_float_env("GLOBAL_DISCOUNT_PERCENT", 0.0)
GLOBAL_DISCOUNT = GLOBAL_DISCOUNT_PERCENT / 100.0
PRICE_MIN_CLP = _get_float_env("PRICE_MIN_CLP", 500.0)

PREFERRED_PROVIDERS = ["cardkingdom"]

CONDITION_MULTIPLIERS = {
    "NM": 1.00,
    "M": 1.00,
    "EX": 0.90,
    "SP": 0.90,
    "VG": 0.80,
    "MP": 0.80,
    "HP": 0.6,
}

# ============================================================
# UTILIDADES
# ============================================================


def normalize(s: str) -> str:
    if not s:
        return ""
    s = "".join(
        c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)
    )
    s = s.lower()
    s = s.replace("’", "'").replace("`", "'")
    s = s.replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace(
        "ú", "u"
    )
    s = s.replace("ñ", "n")
    return s


def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    dest.write_bytes(resp.content)


MTGJSON_DIR = PROJECT_ROOT / "mtgjson"
ALL_IDENTIFIERS_GZ = MTGJSON_DIR / "AllIdentifiers.json.gz"
ALL_PRICES_TODAY_GZ = MTGJSON_DIR / "AllPricesToday.json.gz"


def ensure_mtgjson_files(force: bool = False):
    MTGJSON_DIR.mkdir(parents=True, exist_ok=True)
    if force or not ALL_IDENTIFIERS_GZ.exists():
        print("[INFO] Descargando AllIdentifiers.json.gz...")
        download_file(
            "https://mtgjson.com/api/v5/AllIdentifiers.json.gz", ALL_IDENTIFIERS_GZ
        )
    if force or not ALL_PRICES_TODAY_GZ.exists():
        print("[INFO] Descargando AllPricesToday.json.gz...")
        download_file(
            "https://mtgjson.com/api/v5/AllPricesToday.json.gz", ALL_PRICES_TODAY_GZ
        )


def load_json_gz(path: Path) -> Dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def build_translation_and_indices(identifiers: Dict[str, Any]):
    data = identifiers.get("data") or {}

    es_to_en: Dict[str, str] = {}
    en_to_es: Dict[str, str] = {}
    name_index: Dict[Tuple[str, str], str] = {}
    number_index: Dict[Tuple[str, str], str] = {}

    for uuid, card in data.items():
        en_raw = card.get("name") or ""
        en_norm = normalize(en_raw)
        set_code = (card.get("setCode") or "").upper()
        number = (card.get("number") or "").strip()

        if set_code and en_norm:
            name_index[(set_code, en_norm)] = uuid

        if set_code and number:
            number_index[(set_code, number)] = uuid

        for fd in card.get("foreignData", []) or []:
            if fd.get("language") == "Spanish":
                es_raw = fd.get("name") or ""
                es_norm = normalize(es_raw)
                if es_norm and en_raw:
                    es_to_en[(set_code, es_norm)] = en_raw
                    if (set_code, en_norm) not in en_to_es:
                        en_to_es[(set_code, en_norm)] = es_raw

    return es_to_en, en_to_es, name_index, number_index


def _normalize_name_for_lookup(name: str) -> str:
    return normalize(name)


def resolve_name_to_english(name: str, es_to_en: dict) -> str:
    return es_to_en.get((_normalize_name_for_lookup(name)[0:3], _normalize_name_for_lookup(name)), name)


# ============================================================
# PRECIOS DESDE MTGJSON (POR uuid)
# ============================================================


def get_price_from_mtgjson(
    price_entry: Dict[str, Any], is_foil: bool, condition: str, usd_to_clp: float
):
    """
    Obtiene el precio desde la entrada de MTGJSON y lo convierte a CLP.

    La conversión a CLP se hace SIEMPRE con el parámetro usd_to_clp, que puede
    variar por vendedor. Así cada vendedor puede definir su propio tipo de cambio,
    por ejemplo mediante variables de entorno como FrancoArenas_USD_TO_CLP.
    """
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
    prices_dict = retail.get(finish_key) or retail.get(
        "foil" if finish_key == "normal" else "normal"
    )
    if not prices_dict:
        return None

    try:
        last_date = sorted(prices_dict.keys())[-1]
        base_usd = float(prices_dict[last_date])
    except Exception:
        return None

    cond_mult = CONDITION_MULTIPLIERS.get(condition.upper(), 1.0)
    adj_usd = base_usd * cond_mult
    adj_clp = adj_usd * usd_to_clp

    adj_clp *= (1 - GLOBAL_DISCOUNT)

    if adj_clp < PRICE_MIN_CLP:
        adj_clp = PRICE_MIN_CLP

    return float(f"{adj_usd:.4f}"), float(f"{adj_clp:.2f}"), provider_name or "cardkingdom"


# ============================================================
# ACTUALIZAR INVENTARIOS POR VENDEDOR
# ============================================================


def actualizar_inventarios_vendedores(force_download: bool = False):
    print("[INFO] Preparando archivos MTGJSON...")
    ensure_mtgjson_files(force_download)

    identifiers = load_json_gz(ALL_IDENTIFIERS_GZ)
    prices_data = load_json_gz(ALL_PRICES_TODAY_GZ).get("data", {})

    es_to_en, en_to_es, name_index, number_index = build_translation_and_indices(
        identifiers
    )

    if not SELLER_INVENTORIES_DIR.exists():
        print(
            f"[ERROR] No existe el directorio de inventarios de vendedores: {SELLER_INVENTORIES_DIR}"
        )
        sys.exit(1)

    total_updated = 0
    total_without_price = 0

    for csv_path in sorted(SELLER_INVENTORIES_DIR.glob("*.csv")):
        print(f"[INFO] Leyendo inventario vendedor: {csv_path.name}")
        with csv_path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            headers = reader.fieldnames or []

        if "price_source" not in headers:
            headers.append("price_source")
        if "lock_price" not in headers:
            headers.append("lock_price")
        if "name_en" not in headers:
            headers.append("name_en")
        if "collector_number" not in headers:
            headers.append("collector_number")

        # Determinar tipo de cambio USD->CLP específico por vendedor.
        # Primero intentamos tomar el seller_name desde la primera fila del CSV.
        seller_name_env = ""
        if rows:
            first_row = rows[0]
            seller_name_env = (first_row.get("seller_name") or "").strip()
        if not seller_name_env:
            # Fallback: deducirlo desde el nombre del archivo antes del primer '-'
            seller_name_env = csv_path.stem.split("-", 1)[0]
        env_key = f"{seller_name_env}_USD_TO_CLP".replace(" ", "_")
        seller_usd_to_clp = _get_float_env(env_key, USD_TO_CLP)
        print(
            f"[INFO] Tipo de cambio para {seller_name_env}: {seller_usd_to_clp} (env {env_key} o USD_TO_CLP global)"
        )

        updated = 0
        without_price = 0

        for row in rows:
            # Respetar lock_price / price_source manual
            lock = str(row.get("lock_price", "")).strip().lower()
            if lock in ("1", "true", "yes", "y", "manual"):
                continue
            if str(row.get("price_source", "")).lower() == "manual":
                continue

            name = (row.get("name") or "").strip()
            name_en_csv = (row.get("name_en") or "").strip()
            set_code = (row.get("set") or "").strip().upper()
            lang = (row.get("lang") or "").lower()
            collector_number = (row.get("collector_number") or "").strip()
            condition = (row.get("condition") or "NM").strip().upper()
            is_foil_str = str(row.get("is_foil", "")).lower()
            is_foil = is_foil_str in ("1", "true", "yes", "y", "foil")

            if not name or not set_code:
                row["price_usd_ref"] = ""
                row["price_clp"] = ""
                row["price_source"] = ""
                without_price += 1
                continue

            # Resolver nombre inglés:
            if name_en_csv:
                name_en_raw = name_en_csv
            elif lang == "es":
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

            uuid = None

            # 1) Si tenemos collector_number, intentar (setCode, collector_number)
            if collector_number:
                uuid = number_index.get((set_code, collector_number))

            # 2) Si no hay uuid aún, intentar por nombre inglés normalizado
            if not uuid:
                uuid = name_index.get((set_code, name_en_norm))

            if uuid:
                price_entry = prices_data.get(uuid)
            else:
                price_entry = None

            price_usd = None
            price_clp = None
            source = ""

            if price_entry:
                mtg_result = get_price_from_mtgjson(
                    price_entry, is_foil, condition, seller_usd_to_clp
                )
                if mtg_result:
                    price_usd, price_clp, source = mtg_result

            if price_usd is None:
                row["price_usd_ref"] = ""
                row["price_clp"] = ""
                row["price_source"] = ""
                without_price += 1
            else:
                row["price_usd_ref"] = f"{price_usd:.4f}"
                row["price_clp"] = str(int(round(price_clp)))
                row["price_source"] = source or "cardkingdom"
                updated += 1

        tmp_path = csv_path.with_suffix(".tmp")
        with tmp_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)

        tmp_path.replace(csv_path)

        print(f"[OK] {csv_path.name}:")
        print(f"  Cartas con precio: {updated}")
        print(f"  Cartas sin precio: {without_price}")

        total_updated += updated
        total_without_price += without_price

    print("[OK] Precios actualizados para todos los vendedores.")
    print(f"  TOTAL cartas con precio: {total_updated}")
    print(f"  TOTAL cartas sin precio: {total_without_price}")


if __name__ == "__main__":
    logger = get_logger("actualizar_precios_mtgjson")
    log_info("==== INICIO actualizar_precios_mtgjson ====", logger)
    try:
        # YA VOLVISTE ESTO A True PARA TENER MTGJSON FRESCO SIEMPRE
        actualizar_inventarios_vendedores(force_download=True)
        log_info("==== FIN OK actualizar_precios_mtgjson ====", logger)
    except Exception as e:
        log_exception(e, logger, "actualizar_precios_mtgjson terminó con ERROR")
        raise

