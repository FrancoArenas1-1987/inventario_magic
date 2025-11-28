import os
import base64
import time
import json
import shutil
from pathlib import Path
from typing import Dict, Any, Optional

import requests
from openai import OpenAI

from config_tienda import RAW_DIR, PROCESADAS_DIR

# ========== CONFIGURACIÓN ==========

# Carpeta con las fotos crudas (nombres cualquiera)
# Tomada desde config_tienda.py
# RAW_DIR es un Path

# Carpeta de salida donde dejaremos las fotos ya renombradas (Procesadas)
OUTPUT_DIR = str(PROCESADAS_DIR)

# Archivo de índice para recordar qué imagen cruda ya fue procesada
# (se guarda dentro de OUTPUT_DIR)
INDEX_FILENAME = "renamed_index.json"


# Modelo de visión
OPENAI_VISION_MODEL = "gpt-4.1-mini"

# Endpoints de Scryfall
SCRYFALL_SEARCH_URL = "https://api.scryfall.com/cards/search"
SCRYFALL_NAMED_URL = "https://api.scryfall.com/cards/named"

# Límite de rate de Scryfall (segundos entre llamadas, por seguridad)
SCRYFALL_RATE_LIMIT_SECONDS = 0.12

# Extensiones de imagen permitidas
IMAGE_EXTS = {".jpg", ".jpeg", ".png"}

# Clave OpenAI (se toma de la variable de entorno OPENAI_API_KEY)
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")


# ========== CLIENTE OPENAI ==========

def get_openai_client() -> OpenAI:
    if not OPENAI_API_KEY:
        raise RuntimeError("No se encontró OPENAI_API_KEY en variables de entorno.")
    return OpenAI(api_key=OPENAI_API_KEY)


client = get_openai_client()


# ========== UTILIDADES ==========

def encode_image_to_base64(image_path: Path) -> str:
    with image_path.open("rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def analyze_image_with_vision(image_path: Path) -> Dict[str, Any]:
    """
    Envía la imagen al modelo de visión y pide un JSON con:
    - name_detected: nombre IMPRESO en la carta
    - language: código de idioma ('es', 'en', 'pt', etc.)
    - is_foil: true/false si la carta se ve foil
    - foil_confidence: número entre 0 y 1 que indica qué tan seguro está el modelo de que es foil
    - extra_text: texto adicional (para debug)
    """
    b64 = encode_image_to_base64(image_path)

    prompt = (
        "Analiza esta carta de Magic: The Gathering.\n"
        "Devuélveme SOLO un JSON válido sin texto adicional.\n"
        "Debes ser MUY conservador al marcar una carta como foil.\n"
        "Solo marca \"is_foil\": true si se ve claramente brillo metálico intenso típico de cartas foil; "
        "si tienes dudas, usa false.\n"
        "Formato EXACTO:\n"
        "{\n"
        '  "name_detected": "nombre IMPRESO en la carta, tal como se ve",\n'
        '  "language": "código ISO del idioma impreso, ej: es, en, pt, fr, de, it, ja, ko, ru, zhs, zht",\n'
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
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                    },
                ],
            },
        ],
        temperature=0,
    )

    raw = resp.choices[0].message.content.strip()

    # Por si el modelo igual se pasa de listo y manda ```json ... ```
    if raw.startswith("```"):
        raw = raw.strip()
        # quitar backticks iniciales
        while raw.startswith("```"):
            raw = raw[3:].lstrip()
        # quitar 'json' inicial si está
        if raw.lower().startswith("json"):
            raw = raw[4:].lstrip()
        # quitar backticks finales
        while raw.endswith("```"):
            raw = raw[:-3].rstrip()

    try:
        data = json.loads(raw)
        return data
    except Exception as e:
        print(f"[ERROR] No se pudo parsear JSON desde visión para {image_path.name}: {e}")
        print("Contenido recibido:\n", raw)
        return {}


def fetch_card_from_scryfall(name_detected: str, lang: str) -> Optional[dict]:
    """
    Busca la carta en Scryfall respetando el idioma original.
    - Si lang es reconocido (es, en, pt, fr, etc):
        * intenta /cards/search q=!\"nombre\" lang:<lang> (exacto)
        * si falla, q=nombre lang:<lang> (fuzzy)
    - Si todo falla, intenta /cards/named (exact y fuzzy) como fallback en inglés.
    """
    lang = (lang or "en").lower().strip()

    # 1) Intento con /cards/search usando el idioma detectado
    if lang in ("es", "en", "pt", "fr", "de", "it", "ja", "ko", "ru", "zhs", "zht"):
        query_exact = f'!"{name_detected}" lang:{lang}'
        try:
            resp = requests.get(
                SCRYFALL_SEARCH_URL,
                params={"q": query_exact},
                timeout=12,
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("data"):
                    return data["data"][0]
        except Exception as e:
            print(f"[WARN] Error en Scryfall (search exact): {e}")

        # Fuzzy con idioma
        query_fuzzy = f'{name_detected} lang:{lang}'
        try:
            resp = requests.get(
                SCRYFALL_SEARCH_URL,
                params={"q": query_fuzzy},
                timeout=12,
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("data"):
                    return data["data"][0]
        except Exception as e:
            print(f"[WARN] Error en Scryfall (search fuzzy): {e}")

    # 2) Fallback /cards/named en inglés
    try:
        resp = requests.get(
            SCRYFALL_NAMED_URL,
            params={"exact": name_detected},
            timeout=12,
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        print(f"[WARN] Error en Scryfall (named exact): {e}")

    # 3) Fuzzy /cards/named en inglés
    try:
        resp = requests.get(
            SCRYFALL_NAMED_URL,
            params={"fuzzy": name_detected},
            timeout=12,
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        print(f"[WARN] Error en Scryfall (named fuzzy): {e}")

    return None


def build_new_filename(card_data: dict, lang_detected: str, is_foil: bool, ext: str, existing_filenames: set) -> str:
    """
    Construye un nombre de archivo estándar, asegurando unicidad:
    <Nombre_impreso> - <SET> - <lang> - <COND> - 1.ext

    donde:
    - COND = 'NM'      para cartas normales
    - COND = 'NM_FOIL' para cartas foil

    Usamos '_' dentro de la condición para poder parsear FOIL después sin romper el split por '-'.
    """
    printed_name = card_data.get("printed_name")
    scry_name = card_data.get("name", "CartaDesconocida")

    if printed_name:
        display_name = printed_name
    else:
        display_name = scry_name

    display_name = display_name.replace("/", " // ").strip()

    set_code = (card_data.get("set") or "").upper()

    lang = (lang_detected or card_data.get("lang") or "en").lower()

    cond_segment = "NM_FOIL" if is_foil else "NM"

    base = f"{display_name} - {set_code} - {lang} - {cond_segment} - 1"
    base = " ".join(base.split())

    candidate = f"{base}{ext}"
    counter = 2

    while candidate in existing_filenames:
        candidate = f"{base} ({counter}){ext}"
        counter += 1

    existing_filenames.add(candidate)
    return candidate


def refine_foil_decision(is_foil_vision: bool, foil_confidence: float, card_data: dict) -> bool:
    """
    Refina la decisión de FOIL combinando:
      - Lo que vio el modelo de visión (is_foil_vision + foil_confidence)
      - La información de Scryfall sobre si esta impresión existe en foil

    Objetivo: evitar falsos positivos (cartas normales marcadas como foil).
    Preferimos que una carta foil quede marcada como normal antes que al revés.
    """
    finishes = card_data.get("finishes") or []
    if isinstance(finishes, list):
        finishes_lower = [str(x).lower() for x in finishes]
    else:
        finishes_lower = [str(finishes).lower()]

    scry_foil = bool(card_data.get("foil", False))
    scry_nonfoil = bool(card_data.get("nonfoil", False))

    foil_possible = ("foil" in finishes_lower) or ("etched" in finishes_lower) or scry_foil

    # Si Scryfall dice que esta impresión no existe en foil, nunca la marcamos como foil.
    if not foil_possible:
        return False

    # Si el modelo de visión está muy seguro y Scryfall permite foil, aceptamos foil.
    if is_foil_vision and foil_confidence >= 0.8:
        return True

    # En impresiones que solo existen en foil (sin versión nonfoil), podemos ser un poco
    # más permisivos, pero igual exigimos que la visión la haya visto como foil.
    only_foil = foil_possible and not scry_nonfoil and ("nonfoil" not in finishes_lower)
    if is_foil_vision and only_foil and foil_confidence >= 0.5:
        return True

    # En cualquier otro caso, la tratamos como NO foil para evitar sobrevalorar cartas.
    return False


def ensure_output_dir() -> Path:
    out_path = Path(OUTPUT_DIR)
    out_path.mkdir(parents=True, exist_ok=True)
    return out_path


def load_index(out_path: Path) -> Dict[str, str]:
    """
    Carga el índice raw_rel_path -> processed_filename.
    raw_rel_path es la ruta relativa del archivo crudo respecto a RAW_DIR.
    """
    index_path = out_path / INDEX_FILENAME
    if not index_path.exists():
        return {}
    try:
        return json.loads(index_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[WARN] No se pudo leer {index_path}: {e}")
        return {}


def save_index(out_path: Path, index: Dict[str, str]) -> None:
    index_path = out_path / INDEX_FILENAME
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")


# ========== SCRIPT PRINCIPAL ==========

def main():
    raw_path = Path(RAW_DIR)
    if not raw_path.exists():
        raise SystemExit(f"El directorio RAW no existe: {RAW_DIR}")

    out_path = ensure_output_dir()

    # Cargar índice existente
    index = load_index(out_path)
    print(f"[INFO] Índice cargado con {len(index)} entradas.")

    # Conjunto de nombres de archivo ya existentes en OUTPUT_DIR
    existing_filenames = set()
    for f in out_path.iterdir():
        if f.is_file() and f.name != INDEX_FILENAME:
            existing_filenames.add(f.name)

    # Recorrer todas las imágenes en RAW_DIR
    for root, _, files in os.walk(raw_path):
        for fname in files:
            src = Path(root) / fname
            if not src.is_file():
                continue

            ext = src.suffix.lower()
            if ext not in IMAGE_EXTS:
                continue

            # Ruta relativa respecto a RAW_DIR (para usar como clave estable)
            rel_path = str(src.relative_to(raw_path))

            # Si ya está en el índice, asumimos que ya la procesamos
            if rel_path in index:
                print(f"[SKIP] {rel_path} ya procesada como {index[rel_path]}")
                continue

            print(f"[INFO] Analizando nueva imagen: {rel_path}")

            # 1) Analizar con visión
            vision_data = analyze_image_with_vision(src)
            name_detected = (vision_data.get("name_detected") or "").strip()
            lang = (vision_data.get("language") or "en").strip().lower()
            is_foil_raw = vision_data.get("is_foil", False)

            # Asegurar bool para is_foil (visión)
            if isinstance(is_foil_raw, str):
                is_foil_vision = is_foil_raw.strip().lower() in ("true", "1", "sí", "si", "yes")
            else:
                is_foil_vision = bool(is_foil_raw)

            # Leer foil_confidence si viene en la respuesta
            foil_conf_raw = vision_data.get("foil_confidence", 0)
            try:
                foil_confidence = float(foil_conf_raw)
            except (TypeError, ValueError):
                foil_confidence = 0.0
            if foil_confidence < 0:
                foil_confidence = 0.0
            elif foil_confidence > 1:
                foil_confidence = 1.0

            if not name_detected:
                print(f"[WARN] No se detectó nombre en la imagen {rel_path}, la dejo sin procesar.")
                continue

            print(
                f"      -> Visión detectó name='{name_detected}', lang={lang}, "
                f"is_foil_vision={is_foil_vision}, foil_confidence={foil_confidence}"
            )

            # 2) Buscar en Scryfall
            card_data = fetch_card_from_scryfall(name_detected, lang)
            time.sleep(SCRYFALL_RATE_LIMIT_SECONDS)

            if not card_data:
                print(f"[WARN] No se pudo mapear '{name_detected}' en Scryfall, no se copia.")
                continue

            # 2b) Refinar decisión FOIL usando Scryfall
            is_foil = refine_foil_decision(is_foil_vision, foil_confidence, card_data)
            print(f"      -> Decisión final foil={is_foil}")

            # 3) Construir nombre nuevo respetando idioma original y FOIL
            new_filename = build_new_filename(card_data, lang, is_foil, ext, existing_filenames)
            dst = out_path / new_filename

            # 4) Copiar la imagen (no movemos Raw, la dejamos como origen siempre)
            shutil.copy2(src, dst)
            print(f"[OK] {rel_path} -> {new_filename}")

            # 5) Actualizar índice
            index[rel_path] = new_filename

    # Guardar índice actualizado
    save_index(out_path, index)
    print(f"[INFO] Índice actualizado con {len(index)} entradas totales.")


if __name__ == "__main__":
    main()
