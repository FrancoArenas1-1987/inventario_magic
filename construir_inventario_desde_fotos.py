import csv
import time
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional
import re
import json
from logger_tienda import get_logger, log_info, log_exception

from dotenv import load_dotenv
from config_tienda import PROJECT_ROOT, PROCESADAS_DIR, INVENTORY_ERRORES_CSV, INVENTORY_CSV

# Cargar .env
load_dotenv(PROJECT_ROOT / ".env")

# Directorio donde se guardan los inventarios por vendedor
SELLER_INVENTORIES_DIR: Path = INVENTORY_CSV.parent / "inventarios_vendedores"

# Índice de visión
VISION_INDEX_PATH: Path = PROJECT_ROOT / "vision_index.json"

# Extensiones válidas de imagen
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

# Orden de columnas del CSV por vendedor
HEADERS = [
    "id",
    "item_id",          # ruta relativa imagen en PROCESADAS
    "name",             # nombre en el idioma de la carta
    "name_en",          # nombre oficial en inglés si se conoce
    "set",
    "collector_number", # número de colección (ej: 229)
    "lang",
    "condition",
    "is_foil",
    "format",
    "quantity",
    "price_clp",
    "lock_price",
    "image_url",
    "status",           # Disponible / Vendido
    "price_usd_ref",
    "seller_name",
    "seller_phone",
]


# ===================== UTILIDADES =====================

def infer_seller_from_item_id(item_id: str) -> Tuple[str, str, str]:
    """
    A partir de item_id (ej: 'FrancoArenas-+56990590045/archivo.jpg')
    devuelve (seller_name, seller_phone, seller_folder).

    El seller_folder es exactamente el nombre de la carpeta de PROCESADAS.
    """
    item_id = (item_id or "").strip()
    seller_folder = ""
    if "/" in item_id:
        seller_folder = item_id.split("/", 1)[0].strip()
    else:
        # item_id antiguo sin carpeta -> no podemos inferir bien
        seller_folder = ""

    seller_name = ""
    seller_phone = ""

    if seller_folder:
        # Tomamos la primera separación por '-' para permitir nombres sin teléfono
        if "-" in seller_folder:
            seller_name, seller_phone = seller_folder.split("-", 1)
        else:
            seller_name = seller_folder

    return seller_name.strip(), seller_phone.strip(), seller_folder



def load_vision_index() -> Dict[str, Any]:
    if not VISION_INDEX_PATH.exists():
        return {}
    try:
        with VISION_INDEX_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def normalize_status(status: str) -> str:
    s = (status or "").strip().lower()
    if s in ("disponible", "available", "avail", ""):
        return "Disponible"
    if s in ("vendido", "sold"):
        return "Vendido"
    return status or ""


def parse_filename(filename: str) -> Optional[Dict[str, Any]]:
    """
    Parsea nombres tipo:
        'Mishra's Bauble - 2XM - en - NM - 1.jpg'
        'Círculo de protección: verde -  - es - NM - 20251208_092016.jpg'
    """
    stem = Path(filename).stem
    parts = [p.strip() for p in stem.split(" - ")]

    if len(parts) < 4:
        return None

    name_raw = parts[0]
    set_code = parts[1] or ""
    lang = parts[2] or ""
    condition = parts[3] or "NM"

    quantity = 1
    lower_name = stem.lower()
    is_foil = "foil" in lower_name or "brillante" in lower_name or "foiled" in lower_name

    return {
        "name_raw": name_raw,
        "set_code": set_code.lower(),
        "lang": lang.lower(),
        "condition": condition.upper(),
        "quantity": quantity,
        "is_foil": is_foil,
    }


def append_error(path: Path, row: Dict[str, Any]) -> None:
    new_file = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("a", encoding="utf-8", newline="") as f:
        fieldnames = ["image_url", "error", "extra"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if new_file:
            writer.writeheader()
        writer.writerow(
            {
                "image_url": row.get("image_url", ""),
                "error": row.get("error", ""),
                "extra": row.get("extra", ""),
            }
        )


def load_all_seller_inventories() -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, Any]], int]:
    """
    Carga TODOS los inventarios por vendedor desde SELLER_INVENTORIES_DIR,
    siempre recalculando seller_name y seller_phone desde item_id (carpeta de PROCESADAS),
    y evitando duplicados por item_id.
    """
    existing_by_item: Dict[str, Dict[str, Any]] = {}
    all_rows: List[Dict[str, Any]] = []
    max_id = 0

    if not SELLER_INVENTORIES_DIR.exists():
        return existing_by_item, all_rows, 0

    for csv_path in SELLER_INVENTORIES_DIR.glob("*.csv"):
        with csv_path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                # --- item_id ---
                item_id = (row.get("item_id") or "").strip()

                # Si falta item_id, lo reconstruimos al menos con image_url
                if not item_id:
                    image_url = (row.get("image_url") or "").strip()
                    if not image_url:
                        continue
                    item_id = image_url
                    row["item_id"] = item_id

                # --- seller_name / seller_phone SIEMPRE desde item_id ---
                seller_name, seller_phone, _ = infer_seller_from_item_id(item_id)
                row["seller_name"] = seller_name
                row["seller_phone"] = seller_phone

                # Normalizamos status
                row["status"] = normalize_status(row.get("status", ""))

                # Nos quedamos con la última versión de cada item_id
                existing_by_item[item_id] = row

                # max_id
                try:
                    _id = int((row.get("id") or "0").strip() or "0")
                    if _id > max_id:
                        max_id = _id
                except ValueError:
                    pass

    # all_rows solo con UNA fila por item_id
    all_rows = list(existing_by_item.values())
    return existing_by_item, all_rows, max_id



def write_seller_inventories(all_rows: List[Dict[str, Any]]) -> None:
    """
    Escribe los CSV de inventario por vendedor.

    El vendedor se infiere SIEMPRE desde item_id, que a su vez viene de la
    carpeta de PROCESADAS (seller_folder/archivo.jpg). Así garantizamos que
    el nombre del CSV y los campos seller_name / seller_phone sean coherentes
    con la carpeta real del vendedor.
    """
    SELLER_INVENTORIES_DIR.mkdir(parents=True, exist_ok=True)

    # Opcional pero recomendable: limpiar CSV viejos para no dejar basura
    for old in SELLER_INVENTORIES_DIR.glob("*.csv"):
        try:
            old.unlink()
        except Exception:
            pass

    vendedores: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}

    for r in all_rows:
        item_id = (r.get("item_id") or "").strip()
        seller_name, seller_phone, seller_folder = infer_seller_from_item_id(item_id)

        # Actualizamos siempre estos campos según la carpeta
        r["seller_name"] = seller_name
        r["seller_phone"] = seller_phone

        key = (seller_name, seller_phone)
        vendedores.setdefault(key, []).append(r)

    for (seller_name, seller_phone), v_rows in vendedores.items():
        # Usamos la misma lógica de antes, pero ya con datos coherentes
        # (derivados de la carpeta)
        safe_name = re.sub(r"[^a-zA-Z0-9+]+", "_", seller_name or "sin_nombre")
        safe_phone = re.sub(r"[^0-9+]+", "_", seller_phone or "sin_telefono")
        filename = f"{safe_name}-{safe_phone}.csv"

        out_path = SELLER_INVENTORIES_DIR / filename
        with out_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=HEADERS)
            writer.writeheader()
            for r in v_rows:
                out_row = {h: r.get(h, "") for h in HEADERS}
                writer.writerow(out_row)



# ===================== PROCESO PRINCIPAL =====================

def build_inventory() -> None:
    """
    Construye/actualiza los inventarios por vendedor **sin pisar** lo ya existente.
    Usa:
      - item_id = ruta relativa de la imagen dentro de PROCESADAS_DIR
      - vision_index.json para llenar name_en y collector_number cuando existan
    """
    print(f"[INFO] Leyendo inventarios por vendedor desde: {SELLER_INVENTORIES_DIR}")
    existing_by_item, all_rows, max_id = load_all_seller_inventories()
    print(f"[INFO] Filas existentes en inventarios de vendedores: {len(all_rows)}")

    vision_index = load_vision_index()

    next_id = max_id + 1
    existing_ids = set(existing_by_item.keys())
    new_rows = list(all_rows)

    if not PROCESADAS_DIR.exists():
        print(f"[WARN] PROCESADAS_DIR no existe: {PROCESADAS_DIR}")
        PROCESADAS_DIR.mkdir(parents=True, exist_ok=True)
        write_seller_inventories(new_rows)
        return

    image_files: List[Path] = []
    for p in PROCESADAS_DIR.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in IMAGE_EXTS:
            continue
        image_files.append(p)

    print(f"[INFO] Imágenes encontradas en PROCESADAS: {len(image_files)}")

    for img_path in image_files:
        rel_path = img_path.relative_to(PROCESADAS_DIR)
        item_id = str(rel_path).replace("\\", "/")

        if item_id in existing_ids:
            continue

        seller_name, seller_phone, _ = infer_seller_from_item_id(item_id)

        info = parse_filename(img_path.name)
        if not info:
            append_error(
                INVENTORY_ERRORES_CSV,
                {
                    "image_url": img_path.name,
                    "error": "Nombre de archivo no cumple el patrón esperado",
                    "extra": "",
                },
            )
            continue

        meta = vision_index.get(item_id, {})
        name_en = (meta.get("name_en") or "").strip()
        collector_number = (meta.get("collector_number") or "").strip()

        row: Dict[str, Any] = {h: "" for h in HEADERS}
        row["id"] = str(next_id)
        next_id += 1

        row["item_id"] = item_id
        row["name"] = info["name_raw"]
        row["name_en"] = name_en
        row["set"] = info["set_code"].upper()
        row["collector_number"] = collector_number
        row["lang"] = info["lang"]
        row["condition"] = info["condition"]
        row["is_foil"] = "true" if info["is_foil"] else "false"
        row["format"] = "paper"
        row["quantity"] = str(info["quantity"])

        row["price_clp"] = ""
        row["lock_price"] = ""
        row["price_usd_ref"] = ""

        row["image_url"] = img_path.name
        row["status"] = "Disponible"

        row["seller_name"] = seller_name.strip()
        row["seller_phone"] = seller_phone.strip()

        new_rows.append(row)
        existing_ids.add(item_id)

    write_seller_inventories(new_rows)
    print(f"[OK] Inventarios por vendedor actualizados en: {SELLER_INVENTORIES_DIR}")


if __name__ == "__main__":
    logger = get_logger("construir_inventario_desde_fotos")
    log_info("==== INICIO construir_inventario_desde_fotos ====", logger)
    try:
        build_inventory()
        log_info("==== FIN OK construir_inventario_desde_fotos ====", logger)
    except Exception as e:
        log_exception(e, logger, "construir_inventario_desde_fotos terminó con ERROR")
        raise

