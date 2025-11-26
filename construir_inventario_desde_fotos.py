import csv
import os
import time
import requests
from pathlib import Path
from typing import Optional, Tuple, Dict, List

# ========== CONFIGURACIÓN GENERAL ==========

# Directorio donde están las fotos ya renombradas (Procesadas)
BASE_DIR = r"C:\Users\franc\OneDrive\Magic\MagicCards\Procesadas"

# Archivo de inventario principal
OUTPUT_CSV = "inventario_cartas.csv"

# Archivo de log de errores
ERROR_LOG_CSV = "inventario_errores.csv"

# Tipo de cambio USD -> CLP
USD_TO_CLP = 750  # ajusta según valor del día

# Endpoints Scryfall
SCRYFALL_NAMED_URL = "https://api.scryfall.com/cards/named"
SCRYFALL_SEARCH_URL = "https://api.scryfall.com/cards/search"

# Respeto básico a la API de Scryfall
SCRYFALL_RATE_LIMIT_SECONDS = 0.12

# Extensiones de imagen
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".jfif"}


# ========== FUNCIONES AUXILIARES ==========

def parse_filename(filename: str) -> Tuple[str, Optional[str], str, str, bool, int]:
    """
    Parsear el nombre de archivo para extraer:
      name_raw, set_code, lang, condition, is_foil, quantity

    Formato esperado (el que genera auto_etiquetar_renombrar):
      "Nombre Carta - SET - lang - COND - qty.ext"

    donde:
      COND puede ser:
        "NM"          (normal)
        "NM_FOIL"     (foil)
        "EX", "VG", etc., con o sin "_FOIL"
    """
    name_only = os.path.splitext(filename)[0]
    parts = [p.strip() for p in name_only.split("-")]

    # Defaults
    name_raw = parts[0] if parts else name_only
    set_code = None
    lang = "en"
    condition = "NM"
    is_foil = False
    quantity = 1

    if len(parts) >= 2:
        set_code = parts[1].strip() or None
    if len(parts) >= 3:
        lang_candidate = parts[2].strip().lower()
        if lang_candidate:
            lang = lang_candidate
    if len(parts) >= 4:
        cond_raw = parts[3].strip().upper()  # ej: NM, NM_FOIL, EX_FOIL
        if "FOIL" in cond_raw:
            is_foil = True
            # Eliminar la parte FOIL y los guiones bajos
            base_cond = cond_raw.replace("FOIL", "").replace("_", "").strip()
            condition = base_cond or "NM"
        else:
            condition = cond_raw or "NM"
    if len(parts) >= 5:
        try:
            quantity = int(parts[4].strip())
        except ValueError:
            quantity = 1

    return name_raw, set_code, lang, condition, is_foil, quantity


def fetch_card_from_scryfall(name: str,
                             set_code: Optional[str],
                             lang: str,
                             errors: List[Dict]) -> Optional[dict]:
    """
    Consulta Scryfall intentando respetar idioma y set.
    1) /cards/search con lang
    2) /cards/named exact/fuzzy como fallback.
    """
    lang = (lang or "en").lower().strip()

    # 1) Intento con /cards/search usando el idioma detectado
    if lang in ("es", "en", "pt", "fr", "de", "it", "ja", "ko", "ru", "zhs", "zht"):
        query_exact = f'!"{name}" lang:{lang}'
        try:
            resp = requests.get(
                SCRYFALL_SEARCH_URL,
                params={"q": query_exact},
                timeout=12,
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("data"):
                    if set_code:
                        for c in data["data"]:
                            if c.get("set", "").upper() == set_code.upper():
                                return c
                    return data["data"][0]
        except Exception as e:
            errors.append({
                "image_file": "",
                "parsed_name": name,
                "set_code": set_code or "",
                "reason": f"Error Scryfall search exact ({lang}): {e}",
            })

        query_fuzzy = f'{name} lang:{lang}'
        try:
            resp = requests.get(
                SCRYFALL_SEARCH_URL,
                params={"q": query_fuzzy},
                timeout=12,
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("data"):
                    if set_code:
                        for c in data["data"]:
                            if c.get("set", "").upper() == set_code.upper():
                                return c
                    return data["data"][0]
        except Exception as e:
            errors.append({
                "image_file": "",
                "parsed_name": name,
                "set_code": set_code or "",
                "reason": f"Error Scryfall search fuzzy ({lang}): {e}",
            })

    # 2) Fallback /cards/named exact/fuzzy (normalmente inglés)
    try:
        params = {"exact": name}
        if set_code:
            params["set"] = set_code.lower()
        resp = requests.get(SCRYFALL_NAMED_URL, params=params, timeout=10)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        errors.append({
            "image_file": "",
            "parsed_name": name,
            "set_code": set_code or "",
            "reason": f"Error Scryfall named exact: {e}",
        })

    try:
        params = {"fuzzy": name}
        resp = requests.get(SCRYFALL_NAMED_URL, params=params, timeout=10)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        errors.append({
            "image_file": "",
            "parsed_name": name,
            "set_code": set_code or "",
            "reason": f"Error Scryfall named fuzzy: {e}",
        })

    errors.append({
        "image_file": "",
        "parsed_name": name,
        "set_code": set_code or "",
        "reason": f"No se encontró carta en Scryfall (lang={lang}).",
    })
    return None


def infer_format_from_legalities(legalities: dict) -> str:
    """Intenta inferir un formato principal a partir de legalities."""
    if not legalities:
        return ""

    if legalities.get("modern") == "legal":
        return "Modern"
    if legalities.get("pioneer") == "legal":
        return "Pioneer"
    if legalities.get("legacy") == "legal":
        return "Legacy"
    if legalities.get("vintage") == "legal":
        return "Vintage"
    if legalities.get("commander") == "legal":
        return "Commander"
    if legalities.get("standard") == "legal":
        return "Standard"
    return ""


def estimate_price_with_condition(usd_normal: Optional[str],
                                  usd_foil: Optional[str],
                                  condition: str,
                                  is_foil: bool) -> Tuple[str, str]:
    """
    Ajusta el precio en USD y CLP según:
      - Si la carta es foil o no
      - Su condición
    y aplica un mínimo de 500 CLP cuando la carta tiene precio (>0).

    Lógica:
      - Si is_foil: base = usd_foil o usd_normal
      - Si no:      base = usd_normal o usd_foil
    """
    base_str = None

    if is_foil:
        base_str = usd_foil or usd_normal
    else:
        base_str = usd_normal or usd_foil

    if not base_str:
        return "", ""

    try:
        base_usd = float(base_str)
    except ValueError:
        return "", ""

    CONDITION_MULTIPLIERS = {
        "NM": 1.00,
        "M": 1.00,
        "EX": 0.90,
        "SP": 0.90,
        "VG": 0.80,
        "MP": 0.80,
        "PL": 0.70,
        "HP": 0.60,
    }

    cond = condition.upper()
    factor = CONDITION_MULTIPLIERS.get(cond, 1.0)

    adjusted_usd = base_usd * factor
    adjusted_clp = adjusted_usd * USD_TO_CLP

    # Piso mínimo de precio
    if adjusted_clp > 0 and adjusted_clp < 500:
        adjusted_clp = 500

    price_usd_ref = f"{adjusted_usd:.2f}"
    price_clp = str(int(round(adjusted_clp)))

    return price_usd_ref, price_clp


def build_image_url(file_path: Path) -> str:
    """En este caso solo usamos el nombre de archivo como image_url."""
    return file_path.name


def load_existing_inventory(path: Path) -> Dict[str, Dict[str, str]]:
    """Carga el inventario existente y lo indexa por image_url."""
    if not path.exists():
        return {}

    existing_by_image: Dict[str, Dict[str, str]] = {}
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            image_url = row.get("image_url", "").strip()
            if not image_url:
                continue
            existing_by_image[image_url] = row
    return existing_by_image


def get_max_existing_id(existing_by_image: Dict[str, Dict[str, str]]) -> int:
    max_id = 0
    for row in existing_by_image.values():
        try:
            val = int(row.get("id", "0"))
            if val > max_id:
                max_id = val
        except ValueError:
            continue
    return max_id


def write_error_log(errors: List[Dict[str, str]]):
    if not errors:
        if Path(ERROR_LOG_CSV).exists():
            Path(ERROR_LOG_CSV).unlink()
        print("[OK] No se registraron errores.")
        return

    fieldnames = ["image_file", "parsed_name", "set_code", "reason"]
    with open(ERROR_LOG_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for err in errors:
            row = {
                "image_file": err.get("image_file", ""),
                "parsed_name": err.get("parsed_name", ""),
                "set_code": err.get("set_code", ""),
                "reason": err.get("reason", ""),
            }
            writer.writerow(row)

    print(f"[WARN] Se registraron {len(errors)} errores en {ERROR_LOG_CSV}.")


# ========== SCRIPT PRINCIPAL ==========

def main():
    base_path = Path(BASE_DIR)
    if not base_path.exists():
        raise SystemExit(f"El directorio base no existe: {BASE_DIR}")

    # 1) Cargar inventario existente (para mantener IDs/estado)
    existing_by_image = load_existing_inventory(Path(OUTPUT_CSV))
    max_id = get_max_existing_id(existing_by_image)

    print(f"[INFO] Inventario previo: {len(existing_by_image)} registros, max_id={max_id}")

    rows = []
    used_images = set()
    errors: List[Dict[str, str]] = []

    print(f"[INFO] Escaneando directorio: {BASE_DIR}")

    for root, _, files in os.walk(base_path):
        for fname in files:
            ext = os.path.splitext(fname)[1].lower()
            if ext not in IMAGE_EXTS:
                continue

            file_path = Path(root) / fname
            image_url = build_image_url(file_path)
            used_images.add(image_url)

            print(f"[INFO] Procesando imagen: {fname}")

            try:
                name_raw, set_code, lang, condition, is_foil, quantity = parse_filename(fname)
            except Exception as e:
                errors.append({
                    "image_file": fname,
                    "parsed_name": "",
                    "set_code": "",
                    "reason": f"Error parseando nombre de archivo: {e}",
                })
                continue

            print(f"      -> name='{name_raw}', set={set_code}, lang={lang}, cond={condition}, is_foil={is_foil}, qty={quantity}")

            # 2) Buscar datos en Scryfall
            card_data = fetch_card_from_scryfall(name_raw, set_code, lang, errors)
            time.sleep(SCRYFALL_RATE_LIMIT_SECONDS)

            if card_data:
                printed_name = card_data.get("printed_name")
                scry_name = printed_name or card_data.get("name", name_raw)
                scry_set = card_data.get("set", (set_code.lower() if set_code else "")).lower()
                scry_lang = card_data.get("lang", lang)
                legalities = card_data.get("legalities", {})
                prices = card_data.get("prices", {})

                usd_normal = prices.get("usd") or ""
                usd_foil = prices.get("usd_foil") or ""

                price_usd_ref, price_clp = estimate_price_with_condition(
                    usd_normal, usd_foil, condition, is_foil
                )
                fmt = infer_format_from_legalities(legalities)
            else:
                scry_name = name_raw
                scry_set = (set_code.lower() if set_code else "")
                scry_lang = lang
                fmt = ""
                price_usd_ref, price_clp = "", ""

                errors.append({
                    "image_file": fname,
                    "parsed_name": name_raw,
                    "set_code": set_code or "",
                    "reason": "No se pudo obtener datos de Scryfall, usando fallback.",
                })

            # 3) Recuperar info previa (ID y status)
            prev = existing_by_image.get(image_url)
            if prev:
                card_id = prev.get("id") or "0"
                status = prev.get("status", "available")
            else:
                max_id += 1
                card_id = str(max_id)
                status = "available"

            row = {
                "id": card_id,
                "name": scry_name,
                "set": scry_set,
                "lang": scry_lang,
                "condition": condition,
                "is_foil": "yes" if is_foil else "no",
                "format": fmt,
                "quantity": str(quantity),
                "price_clp": price_clp,
                "image_url": image_url,
                "status": status,
                "price_usd_ref": price_usd_ref,
            }
            rows.append(row)

    # 4) Marcar cartas cuya imagen ya no existe
    removed = [
        img for img in existing_by_image.keys()
        if img not in used_images
    ]
    if removed:
        for img in removed:
            errors.append({
                "image_file": img,
                "parsed_name": "",
                "set_code": "",
                "reason": "La imagen ya no existe en el directorio; carta eliminada del inventario.",
            })
        print(f"[INFO] Se eliminaron {len(removed)} cartas que ya no tienen imagen en disco.")

    # 5) Escribir CSV de salida
    if not rows:
        print("[WARN] No se encontraron imágenes para procesar.")
        write_error_log(errors)
        return

    fieldnames = [
        "id",
        "name",
        "set",
        "lang",
        "condition",
        "is_foil",
        "format",
        "quantity",
        "price_clp",
        "image_url",
        "status",
        "price_usd_ref",
    ]

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f_out:
        writer = csv.DictWriter(f_out, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"[OK] Inventario actualizado en {OUTPUT_CSV} con {len(rows)} cartas.")
    write_error_log(errors)


if __name__ == "__main__":
    main()
