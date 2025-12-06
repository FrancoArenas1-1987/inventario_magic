# auto_etiquetar_renombrar.py
# ---------------------------------------------------------
# Recorre las imágenes en RAW_DIR, usa visión de OpenAI para:
# - Detectar nombre impreso (name_detected)
# - Detectar idioma
# - Detectar código de edición (set_code) desde la propia carta
# - Detectar si es foil o no, con alta exigencia de confianza
#
# Luego consulta Scryfall solo para completar datos de la carta
# (nombre oficial, finishes, etc.) pero NUNCA para el set.
#
# El nombre final del archivo queda:
#   <Nombre> - <SET> - <lang> - <COND> - 1.ext
# donde:
#   - SET viene solo de visión (o queda vacío si no está seguro)
#   - COND = NM o NM_FOIL
#
# Las imágenes renombradas se copian/mueven a PROCESADAS_DIR.
# ---------------------------------------------------------

import os
import sys
import time
import json
import base64
from pathlib import Path
from typing import Dict, Any, Set

import requests

from config_tienda import RAW_DIR, PROCESADAS_DIR, PROJECT_ROOT

# Si usas python-dotenv, puedes cargar el .env aquí
try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass

# OpenAI cliente nuevo (SDK 1.x)
try:
    from openai import OpenAI

    client = OpenAI()
except ImportError:
    client = None

# Modelo de visión a usar (ajusta si quieres otro)
OPENAI_VISION_MODEL = os.getenv("OPENAI_VISION_MODEL", "gpt-4.1-mini")

# Límite de peticiones a Scryfall (respetar 10 req/seg máx; aquí vamos mucho más lento)
SCRYFALL_RATE_LIMIT_SECONDS = 0.12


# ---------------------------------------------------------
# Utilidades básicas
# ---------------------------------------------------------
def encode_image_to_base64(image_path: Path) -> str:
    with image_path.open("rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)

def get_next_available_filename(dest_dir: Path, base_name: str) -> str:
    """
    Si base_name ya existe, genera base_name (2).jpg, base_name (3).jpg, etc.
    """
    candidate = base_name
    stem, ext = os.path.splitext(base_name)
    counter = 2

    # Mientras exista, generar siguiente variante
    while (dest_dir / candidate).exists():
        candidate = f"{stem} ({counter}){ext}"
        counter += 1

    return candidate

# ---------------------------------------------------------
# Visión: detectar nombre, idioma, set y foil
# ---------------------------------------------------------
def analyze_image_with_vision(image_path: Path) -> Dict[str, Any]:
    """
    Envía la imagen al modelo de visión y pide un JSON con:
    - name_detected: nombre IMPRESO en la carta
    - language: código de idioma ('es', 'en', 'pt', etc.)
    - set_code: código corto de la edición impreso en la carta (ej: C17, SOM, A25).
      Si no se ve claro, dejar cadena vacía "".
    - set_confidence: número entre 0 y 1 indicando qué tan seguro está el modelo del set_code.
    - is_foil: true/false si la carta se ve foil
    - foil_confidence: número entre 0 y 1 indicando qué tan seguro está el modelo de que es foil
    - extra_text: texto adicional (para debug)
    """
    if client is None:
        raise RuntimeError("No se pudo importar openai.OpenAI. Instala 'openai' >= 1.0.0 o revisa tu entorno.")

    b64 = encode_image_to_base64(image_path)

    prompt = (
        "Analiza esta carta de Magic: The Gathering. Debes leer lo que aparece impreso en la propia carta.\n"
        "Devuélveme SOLO un JSON válido sin texto adicional.\n"
        "Debes ser MUY conservador al marcar una carta como foil.\n"
        "Solo marca \"is_foil\": true si se ve claramente brillo metálico intenso típico de cartas foil; "
        "si tienes dudas, usa false.\n"
        "Para el set_code, usa el código corto que aparece junto al número de colección, por ejemplo C17, SOM, A25, M12.\n"
        "Si no ves el set_code con suficiente claridad, deja set_code en blanco y set_confidence = 0.\n"
        "Formato EXACTO:\n"
        "{\n"
        '  "name_detected": "nombre IMPRESO en la carta, tal como se ve",\n'
        '  "language": "código ISO del idioma impreso, ej: es, en, pt, fr, de, it, ja, ko, ru, zhs, zht",\n'
        '  "set_code": "código de edición leído de la carta, ej: C17, SOM, A25 (o \\"\")",\n'
        '  "set_confidence": número entre 0 y 1 (ej: 0.0, 0.25, 0.5, 0.75, 1.0) que indica qué tan seguro estás del set_code,\n'
        '  "is_foil": true o false según si la carta se ve evidentemente foil/brillante,\n'
        '  "foil_confidence": número entre 0 y 1 (ej: 0.0, 0.25, 0.5, 0.75, 1.0) que indica qué tan seguro estás de que es foil,\n'
        '  "extra_text": "cualquier texto relevante adicional que veas (puede ir vacío)"\n'
        "}\n"
        "No agregues ``` ni la palabra json ni explicaciones, solo JSON puro."
    )

    resp = client.chat.completions.create(
        model=OPENAI_VISION_MODEL,
        messages=[
            {"role": "system", "content": "Eres un asistente que responde únicamente JSON válido."},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ],
            },
        ],
        temperature=0,
    )

    raw = resp.choices[0].message.content.strip()

    # Por si el modelo igual responde con ```json ... ```
    if raw.startswith("```"):
        raw = raw.strip()
        while raw.startswith("```"):
            raw = raw[3:].lstrip()
        if raw.lower().startswith("json"):
            raw = raw[4:].lstrip()
        while raw.endswith("```"):
            raw = raw[:-3].rstrip()

    try:
        data = json.loads(raw)
        return data
    except Exception as e:
        print(f"[ERROR] No se pudo parsear JSON desde visión para {image_path.name}: {e}")
        print("Contenido recibido:\n", raw)
        return {}


# ---------------------------------------------------------
# Scryfall: obtener datos de la carta (NO el set)
# ---------------------------------------------------------
def fetch_card_from_scryfall(name_detected: str, lang: str) -> Dict[str, Any]:
    """
    Consulta Scryfall para obtener información de la carta:
    - Usa primero búsqueda exacta en el idioma detectado.
    - Si falla, fuzzy en ese idioma.
    - Si sigue fallando, intenta en inglés.
    Se usa para:
      - nombre oficial
      - printed_name
      - finishes (para saber si existe en foil)
    PERO: NUNCA se usa el 'set' que devuelve Scryfall para renombrar.
    """
    base_url = "https://api.scryfall.com/cards/search"

    # 1) Exacto en idioma detectado
    query = f'!"{name_detected}" lang:{lang}'
    for q in [query, f'{name_detected} lang:{lang}', f'!"{name_detected}"', name_detected]:
        try:
            resp = requests.get(base_url, params={"q": q}, timeout=12)
        except Exception:
            continue

        if resp.status_code != 200:
            continue

        data = resp.json()
        if data.get("object") == "list" and data.get("data"):
            return data["data"][0]

    # 2) Intento con /cards/named en inglés
    try:
        resp = requests.get(
            "https://api.scryfall.com/cards/named",
            params={"exact": name_detected},
            timeout=12,
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass

    return {}


# ---------------------------------------------------------
# FOIL: refinar decisión combinando visión + Scryfall
# ---------------------------------------------------------
def refine_foil_decision(is_foil_vision: bool, foil_confidence: float, card_data: dict) -> bool:
    """
    Refina la decisión de FOIL combinando:
      - Lo que vio el modelo de visión (is_foil_vision + foil_confidence)
      - La información de Scryfall sobre si esta impresión existe en foil.

    Objetivo:
      - Evitar falsos positivos (cartas normales marcadas como foil).
      - Preferimos que una carta foil quede marcada como normal antes que al revés.

    Umbral:
      - foil_confidence >= 0.6 para aceptar foil, siempre que Scryfall permita foil.
    """
    finishes = card_data.get("finishes") or []
    if isinstance(finishes, list):
        finishes_lower = [str(x).lower() for x in finishes]
    else:
        finishes_lower = [str(finishes).lower()]

    scry_foil = bool(card_data.get("foil", False))
    scry_nonfoil = bool(card_data.get("nonfoil", False))

    foil_possible = ("foil" in finishes_lower) or ("etched" in finishes_lower) or scry_foil

    # Si Scryfall dice que esta carta no existe en foil, nunca la marcamos como foil
    if not foil_possible:
        return False

    # Caso normal: requerimos alta confianza del modelo de visión
    try:
        fc = float(foil_confidence)
    except (TypeError, ValueError):
        fc = 0.0

    if is_foil_vision and fc >= 0.7:
        return True

    # Casos donde solo existe en foil (sin nonfoil)
    only_foil = foil_possible and not scry_nonfoil and ("nonfoil" not in finishes_lower)
    if is_foil_vision and only_foil and fc >= 0.7:
        return True

    # En cualquier otro caso, la tratamos como NO foil
    return False

def _sanitize_suffix_for_filename(s: str) -> str:
    """
    Limpia un texto para usarlo como sufijo en el nombre de archivo:
    - Deja solo letras, números, guion y guion bajo.
    """
    s = s.strip()
    allowed = []
    for ch in s:
        if ch.isalnum() or ch in ("-", "_"):
            allowed.append(ch)
    return "".join(allowed)

# ---------------------------------------------------------
# Construir nombre de archivo (SET solo desde visión)
# ---------------------------------------------------------
def build_new_filename(
    image_path: Path,
    vision_data: dict,
    card_data: dict,
    lang_detected: str,
    set_code_vision: str,
    set_confidence: float,
    is_foil_vision: bool,
) -> str:
    """
    Construye el nuevo nombre de archivo a partir de:
      - Datos de visión (nombre, idioma, set_code, foil)
      - Datos de Scryfall (para normalizar nombre, finishes, etc.)

    REGLA IMPORTANTE:
      - El SET SOLO se acepta si viene de visión y con alta confianza.
      - Si visión NO detecta bien el set, dejamos el set vacío ("") y
        NUNCA lo rellenamos con heurísticas ni con Scryfall.
    """
    ext = image_path.suffix.lower()

    name_detected = (vision_data.get("name_detected") or "").strip()
    if not name_detected:
        # Si por alguna razón visión no dio nombre, usamos el de Scryfall
        name_detected = (card_data.get("name") or "").strip()

    display_name = name_detected or (card_data.get("name") or "").strip()
    # Normalización de nombres con "//"
    display_name = display_name.replace("///", "//")   # triple slash raro
    display_name = display_name.replace("// //", "//") # doble duplication
    display_name = display_name.replace(" // // ", " // ")
    display_name = " // ".join([p.strip() for p in display_name.split("//")])
    # Reemplazo de slash por algo seguro
    display_name = display_name.replace("/", "-")
    display_name = display_name.replace("\\", "-")

    display_name = display_name.replace("/", " // ").strip()

    # -------------------------------
    # SET: SOLO desde visión
    # -------------------------------
    set_code_vision = (set_code_vision or "").strip().upper()
    try:
        set_conf = float(set_confidence)
    except (TypeError, ValueError):
        set_conf = 0.0

    if set_conf < 0:
        set_conf = 0.0
    elif set_conf > 1:
        set_conf = 1.0

    # Si la visión está lo suficientemente segura (ej. ≥ 0.9), usamos ese set.
    # Si NO, dejamos el set vacío y NO inventamos nada.
    if set_code_vision and 0.9 <= set_conf <= 1.0 and 2 <= len(set_code_vision) <= 5:
        set_code = set_code_vision
    else:
        set_code = ""  # esto significa: "no hay edición conocida"

    # Idioma detectado (visión > Scryfall > default en)
    lang = (lang_detected or card_data.get("lang") or "en").lower()

    # FOIL según lógica de visión + Scryfall (ya la tienes en refine_foil_decision)
    is_foil = is_foil_vision
    cond_segment = "NM_FOIL" if is_foil else "NM"

    # -------------------------------
    # SUFIJO ÚNICO POR FOTO
    # -------------------------------
    # Usamos el nombre original de la imagen (sin extensión) como sufijo único
    raw_stem = image_path.stem  # ej: "20251125_191703"
    safe_suffix = _sanitize_suffix_for_filename(raw_stem)

    # Aunque el set esté vacío, mantenemos la posición del campo
    # Antes: ... - cond_segment - 1
    # Ahora: ... - cond_segment - <sufijo_unico>
    base = f"{display_name} - {set_code} - {lang} - {cond_segment} - {safe_suffix}"
    base = " ".join(base.split())

    candidate = f"{base}{ext}"
    return candidate




# ---------------------------------------------------------
# Proceso principal
# ---------------------------------------------------------
def main() -> None:
    raw_path = Path(RAW_DIR)
    out_path = Path(PROCESADAS_DIR)

    print("===============================================")
    print("  AUTO ETIQUETAR Y RENOMBRAR CARTAS (VISION)  ")
    print("===============================================")
    print(f"[INFO] RAW_DIR       = {raw_path}")
    print(f"[INFO] PROCESADAS_DIR = {out_path}")
    print(f"[INFO] Proyecto root = {PROJECT_ROOT}")
    print("")

    ensure_dir(raw_path)
    ensure_dir(out_path)

    # Para evitar nombres duplicados
    # Para evitar nombres duplicados (considerando TODAS las subcarpetas en PROCESADAS)
    existing_filenames: Set[str] = set(
        p.name
        for p in out_path.glob("**/*")
        if p.is_file()
    )

    image_extensions = {".jpg", ".jpeg", ".png", ".webp", ".jfif"}

    # Buscamos imágenes en RAW, incluyendo subcarpetas (por vendedor)
    images = [
        p
        for p in raw_path.glob("**/*")
        if p.is_file() and p.suffix.lower() in image_extensions
    ]

    if not images:
        print("[WARN] No se encontraron imágenes en RAW_DIR.")
        return

    print(f"[INFO] Se encontraron {len(images)} imágenes para procesar.\n")

    for idx, src in enumerate(sorted(images), start=1):
        rel_path = src.relative_to(raw_path)
        ext = src.suffix.lower()

        # La primera parte del path relativo será la carpeta del vendedor
        # Ej: RAW/Franco-56990590045/foto.jpg -> seller_folder = "Franco-56990590045"
        parts = rel_path.parts
        seller_folder = parts[0] if len(parts) > 1 else None

        print(f"[{idx}/{len(images)}] Procesando {rel_path} ...")

        # 1) Analizar con visión
        vision_data = analyze_image_with_vision(src)
        name_detected = (vision_data.get("name_detected") or "").strip()
        lang = (vision_data.get("language") or "").strip() or "en"
        set_code_vision = (vision_data.get("set_code") or "").strip().upper()
        set_confidence = vision_data.get("set_confidence") or 0.0
        is_foil_vision = bool(vision_data.get("is_foil") or False)
        foil_confidence = vision_data.get("foil_confidence") or 0.0

        # Normalizamos foil_confidence
        try:
            foil_confidence = float(foil_confidence)
        except (TypeError, ValueError):
            foil_confidence = 0.0

        if foil_confidence < 0:
            foil_confidence = 0.0
        elif foil_confidence > 1:
            foil_confidence = 1.0

        if not name_detected:
            print(f"[WARN] No se detectó nombre en la imagen {rel_path}, la dejo sin procesar.\n")
            continue

        print(
            f"      -> Visión detectó name='{name_detected}', lang={lang}, "
            f"set_code_vision={set_code_vision}, set_confidence={set_confidence}, "
            f"is_foil_vision={is_foil_vision}, foil_confidence={foil_confidence}"
        )

        #2) Buscar carta en Scryfall (solo para completar datos, NO para set)
        card_data = fetch_card_from_scryfall(name_detected, lang)
        time.sleep(SCRYFALL_RATE_LIMIT_SECONDS)

        if not card_data:
            print(f"[WARN] No se pudo mapear '{name_detected}' en Scryfall. Se usará solo la info de visión.\n")
            card_data = {}  # 👈 dict vacío, NO hacemos continue


        # 3) Refinar decisión de FOIL combinando visión + Scryfall
        is_foil = refine_foil_decision(is_foil_vision, foil_confidence, card_data)
        print(f"      -> Decisión final foil={is_foil}")

        # 4) Construir nombre nuevo usando SOLO el set de visión
        new_filename = build_new_filename(
            src,
            vision_data=vision_data,
            card_data=card_data,
            lang_detected=lang,
            set_code_vision=set_code_vision,
            set_confidence=set_confidence,
            is_foil_vision=is_foil_vision,
        )

        # Si la imagen venía desde una carpeta de vendedor, preservamos esa estructura:
        # PROCESADAS/Franco-56990590045/<nuevo_nombre>.jpg
        if seller_folder:
            dst = out_path / seller_folder / new_filename
        else:
            # Compatibilidad con imágenes antiguas directamente en RAW/
            dst = out_path / new_filename

        print(f"      -> Nuevo nombre: {new_filename}")
        dst.parent.mkdir(parents=True, exist_ok=True)

        # Mover o copiar. Aquí MOVEMOS desde RAW a PROCESADAS.
        src.replace(dst)
        print(f"      -> Movido a {dst}\n")



if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[INFO] Proceso interrumpido por el usuario.")
        sys.exit(1)
