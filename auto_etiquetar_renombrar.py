import os
import base64
import time
import json
import shutil
from pathlib import Path
from typing import Dict, Any, Optional

import requests
from openai import OpenAI

# ========== CONFIGURACIÓN ==========

# Carpeta con las fotos crudas que OneDrive sincroniza (nombres cualquiera)
RAW_DIR = r"C:\Users\franc\OneDrive\Magic\MagicCards\Raw"

# Carpeta de salida donde dejaremos las fotos ya renombradas
OUTPUT_DIR = r"C:\Users\franc\OneDrive\Magic\MagicCards\Procesadas"

# Archivo de índice para recordar qué imagen cruda ya fue procesada
INDEX_FILENAME = "renamed_index.json"  # se guardará dentro de OUTPUT_DIR

# Modelo de visión
OPENAI_VISION_MODEL = "gpt-4.1-mini"

# Endpoints de Scryfall
SCRYFALL_SEARCH_URL = "https://api.scryfall.com/cards/search"
SCRYFALL_NAMED_URL = "https://api.scryfall.com/cards/named"
SCRYFALL_RATE_LIMIT_SECONDS = 0.12  # ~8 requests/seg

# Extensiones de imagen que vamos a considerar
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".jfif"}

client = OpenAI()


# ========== FUNCIONES AUXILIARES ==========

def encode_image_to_base64(path: Path) -> str:
    with path.open("rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def analyze_image_with_vision(image_path: Path) -> Dict[str, Any]:
    """
    Envía la imagen al modelo de visión y pide un JSON con:
    - name_detected: nombre IMPRESO en la carta
    - language: código de idioma ('es', 'en', 'pt', etc.)
    - is_foil: true/false si la carta se ve foil
    - extra_text: texto adicional (para debug)
    """
    b64 = encode_image_to_base64(image_path)

    prompt = (
        "Analiza esta carta de Magic: The Gathering.\n"
        "Devuélveme SOLO un JSON válido sin texto adicional.\n"
        "Formato EXACTO:\n"
        "{\n"
        '  \"name_detected\": \"nombre IMPRESO en la carta, tal como se ve\",\n'
        '  \"language\": \"código ISO del idioma impreso, ej: es, en, pt, fr, de, it, ja, ko, ru, zhs, zht\",\n'
        '  \"is_foil\": true o false según si la carta es foil/brillante,\n'
        '  \"extra_text\": \"cualquier texto relevante adicional que veas (puede ir vacío)\"\n'
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
            print(f"[WARN] Error Scryfall search exact ({lang}) para '{name_detected}': {e}")

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
            print(f"[WARN] Error Scryfall search fuzzy ({lang}) para '{name_detected}': {e}")

    # 2) Fallback: intentar con /cards/named asumiendo inglés
    try:
        resp = requests.get(
            SCRYFALL_NAMED_URL,
            params={"exact": name_detected},
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        print(f"[WARN] Error Scryfall named exact para '{name_detected}': {e}")

    try:
        resp = requests.get(
            SCRYFALL_NAMED_URL,
            params={"fuzzy": name_detected},
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        print(f"[WARN] Error Scryfall named fuzzy para '{name_detected}': {e}")

    print(f"[INFO] No se encontró carta en Scryfall para '{name_detected}' (lang={lang})")
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

    set_code = (card_data.get("set", "") or "SET").upper()
    lang_scry = (card_data.get("lang") or "").lower().strip()
    if not lang_scry:
        lang_scry = (lang_detected or "en").lower().strip()

    cond_segment = "NM_FOIL" if is_foil else "NM"

    invalid_chars = r'<>:"/\|?*'
    safe_name = "".join("_" if c in invalid_chars else c for c in display_name)

    base = f"{safe_name} - {set_code} - {lang_scry} - {cond_segment} - 1"
    candidate = f"{base}{ext}"
    counter = 2

    while candidate in existing_filenames:
        candidate = f"{base} ({counter}){ext}"
        counter += 1

    existing_filenames.add(candidate)
    return candidate


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

            # Asegurar bool para is_foil
            if isinstance(is_foil_raw, str):
                is_foil = is_foil_raw.strip().lower() in ("true", "1", "sí", "si", "yes")
            else:
                is_foil = bool(is_foil_raw)

            if not name_detected:
                print(f"[WARN] No se detectó nombre en la imagen {rel_path}, la dejo sin procesar.")
                continue

            print(f"      -> Visión detectó name='{name_detected}', lang={lang}, is_foil={is_foil}")

            # 2) Buscar en Scryfall
            card_data = fetch_card_from_scryfall(name_detected, lang)
            time.sleep(SCRYFALL_RATE_LIMIT_SECONDS)

            if not card_data:
                print(f"[WARN] No se pudo mapear '{name_detected}' en Scryfall, no se copia.")
                continue

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
    print(f"[OK] Proceso terminado. Índice actualizado con {len(index)} entradas.")


if __name__ == "__main__":
    main()
