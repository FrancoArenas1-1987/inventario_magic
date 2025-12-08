import csv
import time
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List
import re
import os
import requests

from config_tienda import PROCESADAS_DIR, INVENTORY_CSV, INVENTORY_ERRORES_CSV
from dotenv import load_dotenv

from config_tienda import PROJECT_ROOT, INVENTORY_CSV

# Cargar .env desde la carpeta del proyecto
load_dotenv(PROJECT_ROOT / ".env")

# ============================================================
#  MODO A: "Procesadas como verdad"
#  - El stock (quantity) se obtiene SIEMPRE del nombre del archivo
#    en la carpeta Procesadas.
#  - El CSV solo guarda:
#       - id
#       - status
#       - datos calculados (precio, formato, etc.)
# ============================================================

SCRYFALL_API = "https://api.scryfall.com"
USD_TO_CLP = float(os.getenv("USD_TO_CLP", 900))

# Multiplicadores por condición
CONDITION_MULTIPLIERS = {
    "NM": 1.0,
    "EX": 0.9,
    "SP": 0.8,
    "MP": 0.7,
    "HP": 0.5,
}

# Orden de columnas del CSV
HEADERS = [
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

    # NUEVO: datos del vendedor
    "seller_name",
    "seller_phone",
]

SELLER_INVENTORIES_DIR: Path = INVENTORY_CSV.parent / "inventarios_vendedores"

# ========== UTILIDADES BÁSICAS ==========

def safe_float(v: Any) -> Optional[float]:
    try:
        if v in ("", None):
            return None
        return float(v)
    except (ValueError, TypeError):
        return None


def estimate_price_with_condition(usd_normal: Optional[str],
                                  usd_foil: Optional[str],
                                  condition: str,
                                  is_foil: bool) -> Tuple[str, str]:
    """
    Devuelve (price_usd_ref, price_clp) siempre que exista ALGÚN precio en USD.
    Si no hay ningún precio USD -> ("", "") y el front mostrará "Consultar".
    """

    # Elegimos base según foil / no foil, pero sin matar el precio
    base_str = None

    if is_foil:
        base_str = usd_foil or usd_normal
    else:
        base_str = usd_normal or usd_foil

    if not base_str:
        return "", ""   # no hay ningún precio USD disponible

    try:
        base_usd = float(base_str)
    except ValueError:
        return "", ""

    CONDITION_MULTIPLIERS = {
        "NM": 1.00, "M": 1.00,
        "EX": 0.90, "SP": 0.90,
        "VG": 0.80, "MP": 0.80,
        "HP": 0.60, "POOR": 0.40,
    }

    cond_key = (condition or "NM").upper()
    multiplier = CONDITION_MULTIPLIERS.get(cond_key, 1.0)

    adjusted_usd = base_usd * multiplier
    adjusted_clp = adjusted_usd * USD_TO_CLP

    # Piso mínimo de precio
    if adjusted_clp > 0 and adjusted_clp < 500:
        adjusted_clp = 500

    price_usd_ref = f"{adjusted_usd:.2f}"
    price_clp = str(int(round(adjusted_clp)))

    return price_usd_ref, price_clp


def pick_format(legalities: Dict[str, str]) -> str:
    priority = [
        "modern",
        "pioneer",
        "legacy",
        "vintage",
        "commander",
        "standard",
        "pauper",
        "alchemy",
        "historic",
    ]
    for fmt in priority:
        if legalities.get(fmt) == "legal":
            return fmt.capitalize()
    return "Casual"


def parse_filename(filename: str) -> Optional[Dict[str, Any]]:
    """
    Espera nombres del tipo:

        Nombre de Carta - SET - lang - COND[_FOIL] - qty.ext

    Ejemplos válidos:

        Mishra's Bauble - 2XM - en - NM - 1.jpg
        Mishra's Bauble - 2XM - en - NM - 4.png

    Y TAMBIÉN acepta duplicados generados por Windows, por ejemplo:

        Mishra's Bauble - 2XM - en - NM - 1 (2).jpg
        Mishra's Bauble - 2XM - en - NM - 1 (3).jpg
        Mishra's Bauble - 2XM - en - NM - 1 (4).jpg

    Además ahora soporta correctamente el caso:

        Nihil Spellbomb - - en - NM - 1.jpg

    donde el SET viene vacío.
    """

    stem = Path(filename).stem
    parts = [p.strip() for p in stem.split(" - ")]

    # Caso normal: al menos 5 partes => nombre, set, lang, cond, qty
    if len(parts) >= 5:
        name_raw, set_code, lang, cond_part, qty_str = parts[:5]

    else:
        # Caso especial detectado en inventario_cartas_errores:
        #   "<Nombre> - - es - NM - 1"
        # se estaba parseando como:
        #   ['<Nombre>', '- es', 'NM', '1']
        #
        # Aquí lo interpretamos como:
        #   set_code = ""  (vacío)
        #   lang     = "es" / "en" / etc.
        if len(parts) == 4 and parts[1].startswith("-"):
            name_raw = parts[0]
            set_code = ""  # set vacío
            lang = parts[1].lstrip("-").strip()  # "- es" -> "es"
            cond_part = parts[2]
            qty_str = parts[3]
        else:
            # Cualquier otro formato raro se sigue marcando como error
            return None

    is_foil = False
    cond_upper = cond_part.upper()
    if cond_upper.endswith("_FOIL"):
        is_foil = True
        cond_upper = cond_upper.replace("_FOIL", "")

    # Extraer SOLO el primer número de qty_str
    # Ej: "1" -> 1, "1 (2)" -> 1, "4 copia" -> 4
    m = re.match(r"(\d+)", qty_str)
    if not m:
        return None

    try:
        quantity = int(m.group(1))
    except ValueError:
        return None

    return {
        "name_raw": name_raw,
        "set_code": (set_code or "").lower(),
        "lang": (lang or "").lower(),
        "condition": cond_upper,
        "is_foil": is_foil,
        "quantity": quantity,
    }




# ========== SCRYFALL ==========

def choose_best_scryfall_card(
    candidates: List[Dict[str, Any]],
    set_code: str,
    lang: str,
) -> Optional[Dict[str, Any]]:
    """
    Elige la mejor impresión entre las devueltas por Scryfall.

    Criterios de score:
    - +10 si set coincide exactamente.
    - +5 si lang coincide.
    - +3 si tiene precio en usd o usd_foil.
    - +1 si set_type es 'core' o 'expansion'.
    """
    if not candidates:
        return None

    set_code = (set_code or "").lower()
    lang = (lang or "").lower()

    best = None
    best_score = -1

    for card in candidates:
        # ignorar tokens / cosas raras si se marca en set_type
        set_type = (card.get("set_type") or "").lower()
        if set_type == "token":
            continue

        score = 0
        if (card.get("set") or "").lower() == set_code:
            score += 10
        if (card.get("lang") or "").lower() == lang:
            score += 5

        prices = card.get("prices") or {}
        usd_normal = safe_float(prices.get("usd"))
        usd_foil = safe_float(prices.get("usd_foil"))
        if usd_normal is not None or usd_foil is not None:
            score += 3

        if set_type in ("core", "expansion"):
            score += 1

        if score > best_score:
            best_score = score
            best = card

    return best


def scryfall_search(name: str, set_code: str, lang: str) -> Optional[Dict[str, Any]]:
    """
    Busca la carta en Scryfall con la siguiente estrategia:

    1) Intentar encontrar la impresión en el idioma indicado (lang) y set (set_code).
    2) Si no se encuentra:
       - Buscar la carta en el mismo set, SIN filtrar por idioma.
       - De los resultados, preferir impresiones en INGLÉS con precio.
    3) Si aún así no se encuentra nada:
       - Buscar globalmente por nombre, priorizando impresiones en INGLÉS con precio.

    En resumen: siempre que sea posible, hace fallback a la impresión en inglés
    (especialmente del mismo set) para asegurar que haya precio.
    """
    import requests

    name = (name or "").strip()
    set_code = (set_code or "").strip().lower()
    lang = (lang or "").strip().lower()

    if not name:
        return None

    def has_price(card: Dict[str, Any]) -> bool:
        prices = card.get("prices") or {}
        return any(
            prices.get(k) not in (None, "", "0", "0.0")
            for k in ("usd", "usd_foil", "usd_etched", "eur")
        )

    def choose_best_printing(cards: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """
        De una lista de cartas de Scryfall:
        - Filtra cartas de juego físico (game:paper) y no digitales.
        - Prioriza:
            1) idioma inglés con precio
            2) idioma inglés sin precio
            3) cualquier otro idioma con precio
            4) cualquier otra carta
        """
        physical = [
            c for c in cards
            if not c.get("digital") and "paper" in (c.get("games") or [])
        ]
        if not physical:
            physical = cards

        en_priced = [c for c in physical if c.get("lang") == "en" and has_price(c)]
        if en_priced:
            return en_priced[0]

        en_any = [c for c in physical if c.get("lang") == "en"]
        if en_any:
            return en_any[0]

        other_priced = [c for c in physical if has_price(c)]
        if other_priced:
            return other_priced[0]

        return physical[0] if physical else None

    def run_search_query(q: str) -> Optional[Dict[str, Any]]:
        try:
            resp = requests.get(
                f"{SCRYFALL_API}/cards/search",
                params={"q": q},
                timeout=10,
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            cards = data.get("data") or []
            if not cards:
                return None
            return choose_best_printing(cards)
        except Exception:
            return None

    # 1) Intentar con idioma original + set (si tengo ambos)
    if set_code and lang:
        # nombre exacto
        q = f'!"{name}" set:{set_code} lang:{lang} game:paper -is:token'
        best = run_search_query(q)
        if best:
            return best

        # nombre normal
        q = f'{name} set:{set_code} lang:{lang} game:paper -is:token'
        best = run_search_query(q)
        if best:
            return best

    # 2) Fallback: MISMO set, sin idioma (preferirá inglés en choose_best_printing)
    if set_code:
        # nombre exacto
        q = f'!"{name}" set:{set_code} game:paper -is:token'
        best = run_search_query(q)
        if best:
            return best

        # nombre normal
        q = f'{name} set:{set_code} game:paper -is:token'
        best = run_search_query(q)
        if best:
            return best

    # 3) Búsqueda global HINT: aún puede encontrar impresiones en inglés de otros sets
    # 3.a) nombre exacto global
    q = f'!"{name}" game:paper -is:token'
    best = run_search_query(q)
    if best:
        return best

    # 3.b) nombre normal global
    q = f'{name} game:paper -is:token'
    best = run_search_query(q)
    if best:
        return best

    # 4) Último recurso: /cards/named (exact y fuzzy), que también suele devolver inglés
    named_endpoint = f"{SCRYFALL_API}/cards/named"
    attempts = []

    if set_code:
        attempts.append({"exact": name, "set": set_code})
        attempts.append({"fuzzy": name, "set": set_code})

    attempts.append({"exact": name})
    attempts.append({"fuzzy": name})

    for params in attempts:
        try:
            resp = requests.get(named_endpoint, params=params, timeout=10)
            if resp.status_code != 200:
                continue
            data = resp.json()
            if data:
                # /cards/named devuelve una sola carta
                return data
        except Exception:
            continue

    return None




def compute_foil_flags(card_data: Dict[str, Any]) -> Tuple[bool, bool]:
    """
    Devuelve (has_foil, has_nonfoil) según los datos de Scryfall para la impresión.
    """
    finishes = card_data.get("finishes") or []
    has_foil = "foil" in finishes or card_data.get("foil", False)
    has_nonfoil = "nonfoil" in finishes or card_data.get("nonfoil", False)
    return bool(has_foil), bool(has_nonfoil)


def adjust_is_foil_with_scryfall(is_foil: bool, card_data: Dict[str, Any]) -> bool:
    """
    Corrige la bandera de foil según los datos de la impresión en Scryfall.
    - Si la impresión NO existe en foil, se fuerza a False.
    """
    has_foil, has_nonfoil = compute_foil_flags(card_data)

    # No hay foil para esta impresión
    if not has_foil and has_nonfoil:
        return False

    # Si el archivo venía como foil pero la impresión no soporta foil → no foil
    if is_foil and not has_foil:
        return False

    return is_foil


def compute_price_for_card(
    card_data: Dict[str, Any],
    condition: str,
    is_foil: bool,
) -> Tuple[float, float]:
    """
    Lógica refinada de precios:

    - Usa siempre que se pueda el precio específico (usd_foil / usd).
    - Si la carta es foil y la impresión también existe en nonfoil,
      marcamos el precio como "no confiable" → se devuelve price_clp = 0
      para que en la web aparezca "Consultar", pero dejamos price_usd_ref
      como referencia interna.
    """
    prices = card_data.get("prices") or {}
    usd_normal = safe_float(prices.get("usd"))
    usd_foil = safe_float(prices.get("usd_foil"))

    has_foil, has_nonfoil = compute_foil_flags(card_data)

    # Elegir precio base según foil / no foil
    usd_base: Optional[float] = None
    price_reliable = True

    if is_foil:
        if usd_foil is not None:
            usd_base = usd_foil
            # Si además existe versión nonfoil, consideramos el precio "dudoso"
            # y preferimos mostrar "Consultar" en la web.
            if has_nonfoil:
                price_reliable = False
        elif usd_normal is not None:
            # No hay precio foil específico. Usar normal como referencia,
            # pero marcar como no confiable para que la web muestre "Consultar".
            usd_base = usd_normal
            price_reliable = False
    else:
        if usd_normal is not None:
            usd_base = usd_normal
        elif usd_foil is not None:
            # Solo existe precio foil, pero la carta se marcó como no foil.
            # También lo consideramos poco confiable.
            usd_base = usd_foil
            price_reliable = False

    prices = card_data.get("prices", {})
    usd_normal = prices.get("usd") or ""
    usd_foil = prices.get("usd_foil") or ""

    # (Opcional) ajustar is_foil con la info de Scryfall
    is_foil = adjust_is_foil_with_scryfall(is_foil, card_data)

    price_usd_ref, price_clp = estimate_price_with_condition(
        usd_normal, usd_foil, condition, is_foil
    )

    # Si el precio no es confiable (ej. foil con versión nonfoil),
    # seteamos price_clp = 0 para que la web muestre "Consultar",
    # pero mantenemos price_usd_ref como referencia.
    if not price_reliable:
        return 0.0, price_usd_ref

    return price_clp, price_usd_ref


# ========== INVENTARIO EXISTENTE ==========

def load_existing_inventory(path: Path) -> Tuple[Dict[str, Dict[str, Any]], int]:
    """
    Carga el inventario actual en un dict indexado por image_url.
    También devuelve el max_id encontrado para seguir incrementando.
    """
    existing: Dict[str, Dict[str, Any]] = {}
    max_id = 0
    if not path.exists():
        return existing, 0

    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            image_url = row.get("image_url", "").strip()
            if not image_url:
                continue
            existing[image_url] = row
            try:
                _id = int(row.get("id", "0") or "0")
                if _id > max_id:
                    max_id = _id
            except ValueError:
                continue

    return existing, max_id


def write_inventory(path: Path, rows: List[Dict[str, Any]]) -> None:
    """
    Escribe el CSV de inventario con las filas entregadas.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=HEADERS)
        writer.writeheader()
        for r in rows:
            out = {h: r.get(h, "") for h in HEADERS}
            writer.writerow(out)

def write_seller_inventories(rows: List[Dict[str, Any]]) -> None:
    """
    Genera un CSV de inventario por cada vendedor distinto (seller_name + seller_phone).

    - Solo incluye filas con status != "removed".
    - Crea los archivos en la carpeta `inventarios_vendedores` al lado de INVENTORY_CSV.
    - Nombre de archivo: inventario_<slug_vendedor>.csv
    """
    SELLER_INVENTORIES_DIR.mkdir(parents=True, exist_ok=True)

    vendedores: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}

    for r in rows:
        status = (r.get("status") or "").lower()
        if status == "removed":
            # No incluimos cartas eliminadas en los inventarios por vendedor
            continue

        seller_name = (r.get("seller_name") or "").strip()
        seller_phone = (r.get("seller_phone") or "").strip()

        key = (seller_name, seller_phone)
        vendedores.setdefault(key, []).append(r)

    for (seller_name, seller_phone), v_rows in vendedores.items():
        if not seller_name and not seller_phone:
            filename = "inventario_sin_vendedor.csv"
        else:
            base = f"{seller_name or 'Vendedor'}-{seller_phone or 'sin_telefono'}"
            # slug muy simple: letras, números, _ y -
            slug = re.sub(r"[^0-9A-Za-z_-]+", "_", base)
            filename = f"inventario_{slug}.csv"

        out_path = SELLER_INVENTORIES_DIR / filename
        with out_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=HEADERS)
            writer.writeheader()
            for r in v_rows:
                writer.writerow({h: r.get(h, "") for h in HEADERS})


def append_error(path: Path, row: Dict[str, Any]) -> None:
    """
    Agrega una fila al CSV de errores.
    """
    new_file = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["image_url", "error", "extra"])
        if new_file:
            writer.writeheader()
        writer.writerow(row)

def to_float_or_zero(v):
    try:
        return float(v)
    except Exception:
        return 0.0

def to_int_or_zero(v):
    try:
        return int(float(v))
    except Exception:
        return 0

# ========== CONSTRUCCIÓN DE INVENTARIO ==========

def build_inventory():
    base_path = PROCESADAS_DIR
    if not base_path.exists():
        print(f"[ERROR] PROCESADAS_DIR no existe: {base_path}")
        return

    print(f"[INFO] Construyendo inventario desde: {base_path}")
    # Reiniciar archivo de errores en cada corrida
    if INVENTORY_ERRORES_CSV.exists():
        INVENTORY_ERRORES_CSV.unlink()

    existing_by_image, max_id = load_existing_inventory(INVENTORY_CSV)
    next_id = max_id + 1

    new_rows: List[Dict[str, Any]] = []
    seen_images = set()

    # Cache en memoria para evitar llamadas repetidas a Scryfall por la misma carta
    scryfall_cache: Dict[Tuple[str, str, str], Optional[Dict[str, Any]]] = {}

    # Ahora buscamos imágenes en TODAS las subcarpetas de PROCESADAS
    image_files = sorted(
        [
            p
            for p in base_path.rglob("*")
            if p.is_file() and p.suffix.lower() in [".jpg", ".jpeg", ".png"]
        ],
        key=lambda p: p.name.lower(),
    )

    for img_path in image_files:
        # Ejemplo de ruta:
        # PROCESADAS/Franco-56990590045/Mishra's Bauble - 2XM - en - NM - 1.jpg
        rel_path = img_path.relative_to(base_path)
        parts = rel_path.parts

        seller_folder = parts[0] if len(parts) > 1 else None

        seller_name = ""
        seller_phone = ""

        if seller_folder:
            # Tomamos el último tramo como teléfono y el resto como nombre.
            # Ej: "Franco-56990590045" -> name="Franco" phone="56990590045"
            segments = seller_folder.split("-")
            if len(segments) >= 2:
                seller_phone = segments[-1].lstrip("+").strip()
                seller_name = "-".join(segments[:-1]).strip()

        image_name = img_path.name
        seen_images.add(image_name)

        info = parse_filename(image_name)
        if not info:
            append_error(
                INVENTORY_ERRORES_CSV,
                {
                    "image_url": image_name,
                    "error": "Nombre de archivo no cumple el patrón esperado",
                    "extra": "",
                },
            )
            continue

        # Guardamos también los datos del vendedor dentro de info,
        # para usarlos más abajo al construir la fila del CSV.
        info["seller_name"] = seller_name
        info["seller_phone"] = seller_phone

        # ---- NUEVO: cache de Scryfall por (name_raw, set_code, lang) ----
        cache_key = (info["name_raw"], info["set_code"], info["lang"])
        if cache_key in scryfall_cache:
            card_data = scryfall_cache[cache_key]
        else:
            card_data = scryfall_search(info["name_raw"], info["set_code"], info["lang"])
            scryfall_cache[cache_key] = card_data
            # Pequeño delay por respeto a la API de Scryfall (solo cuando llamamos a la API)
            time.sleep(0.05)

        if not card_data:
            append_error(
                INVENTORY_ERRORES_CSV,
                {
                    "image_url": image_name,
                    "error": "No se pudo mapear en Scryfall",
                    "extra": info["name_raw"],
                },
            )
            continue

        name = card_data.get("printed_name") or card_data.get("name") or info["name_raw"]
        set_code = card_data.get("set", info["set_code"]).upper()
        lang = card_data.get("lang", info["lang"]).lower()
        legalities = card_data.get("legalities") or {}
        fmt = pick_format(legalities)

        is_foil_adj = adjust_is_foil_with_scryfall(info["is_foil"], card_data)

        price_clp, price_usd_ref = compute_price_for_card(
            card_data,
            condition=info["condition"],
            is_foil=is_foil_adj,
        )

        base_row = {
            "id": "",
            "name": name,
            "set": set_code,
            "lang": lang,
            "condition": info["condition"],
            "is_foil": "true" if is_foil_adj else "false",
            # Stock SIEMPRE desde el NOMBRE DEL ARCHIVO
            "quantity": info["quantity"],
            "format": fmt,
            "price_clp": (
                str(to_int_or_zero(price_clp))
                if to_int_or_zero(price_clp) > 0
                else ""
            ),
            "image_url": image_name,
            "status": "available",
            "price_usd_ref": (
                f"{to_float_or_zero(price_usd_ref):.2f}"
                if to_float_or_zero(price_usd_ref) > 0
                else ""
            ),

            # NUEVO: datos del vendedor
            "seller_name": info.get("seller_name", ""),
            "seller_phone": info.get("seller_phone", ""),
        }

        existing = existing_by_image.get(image_name)
        if existing:
            # Mantener ID y status desde el CSV
            base_row["id"] = existing.get("id", "") or ""
            base_row["status"] = existing.get("status", "") or "available"
        else:
            # Carta nueva: asignamos un nuevo ID
            base_row["id"] = str(next_id)
            next_id += 1

        new_rows.append(base_row)

    # Marcar como "removed" las imágenes que estaban en el CSV y ya no existen en Procesadas
    for image_url, row in existing_by_image.items():
        if image_url not in seen_images:
            status = (row.get("status") or "").lower()
            if status != "removed":
                row_copy = dict(row)
                row_copy["status"] = "removed"
                new_rows.append(row_copy)

    write_inventory(INVENTORY_CSV, new_rows)
    print(f"[OK] Inventario generado en: {INVENTORY_CSV}")

    # NUEVO: generar inventarios separados por vendedor
    write_seller_inventories(new_rows)
    print(f"[OK] Inventarios por vendedor generados en: {SELLER_INVENTORIES_DIR}")


if __name__ == "__main__":
    build_inventory()
