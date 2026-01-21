# auto_etiquetar_renombrar.py
# ---------------------------------------------------------
# Recorre las imágenes en RAW_DIR (incluyendo subcarpetas de vendedores),
# llama a visión para:
#   - nombre impreso (name_detected, en el idioma de la carta)
#   - idioma (language)
#   - código de edición (set_code) SOLO si el modelo está seguro
#   - nombre oficial en inglés (name_en) si logra identificarlo
#   - número de colección (collector_number) si logra leerlo
#   - foil / no foil
#
# Luego mueve la imagen a PROCESADAS con nombre:
#   <Nombre> - <SET> - <lang> - <COND> - <original_id>.ext
#
# Además actualiza un índice JSON en PROJECT_ROOT:
#   vision_index.json
# con clave = item_id (ruta relativa en PROCESADAS) y valor con:
#   name_detected, language, set_code, set_confidence,
#   is_foil, foil_confidence, name_en, collector_number
# ---------------------------------------------------------

import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, Any, List, Optional
import base64  
import hashlib  # <-- NUEVO
from dotenv import load_dotenv
from PIL import Image, ExifTags
import re
from logger_tienda import get_logger, log_info, log_error, log_exception
from config_tienda import RAW_DIR, PROCESADAS_DIR, PROJECT_ROOT

# OpenAI
try:
    from openai import OpenAI

    load_dotenv(PROJECT_ROOT / ".env")
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY_MAGIC", "")
    if OPENAI_API_KEY:
        client = OpenAI(api_key=OPENAI_API_KEY)
    else:
        client = None
except Exception:
    client = None

OPENAI_VISION_MODEL = os.getenv("OPENAI_VISION_MODEL", "gpt-4.1-mini")
OPENAI_API_MAX_RETRIES = 3
OPENAI_API_RETRY_DELAY = 5

VISION_INDEX_PATH: Path = PROJECT_ROOT / "vision_index.json"


def compute_image_id(image_path: Path) -> str:
    """
    Genera un ID estable para la imagen usando SHA1 del contenido.
    No depende del nombre original del archivo.
    """
    with open(image_path, "rb") as f:
        data = f.read()
    # Usamos los primeros 10 caracteres para que sea corto pero único
    return hashlib.sha1(data).hexdigest()[:10]

# ---------------------------------------------------------
# Utilidades índice de visión
# ---------------------------------------------------------
def load_vision_index() -> Dict[str, Any]:
    if not VISION_INDEX_PATH.exists():
        return {}
    try:
        with VISION_INDEX_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_vision_index(index: Dict[str, Any]) -> None:
    try:
        with VISION_INDEX_PATH.open("w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[WARN] No se pudo guardar vision_index.json: {e}")


# ---------------------------------------------------------
# Utilidades de imagen (orientación)
# ---------------------------------------------------------
def fix_image_orientation(image_path: Path) -> None:
    """Corrige orientación usando EXIF Orientation si existe."""
    try:
        img = Image.open(image_path)
    except Exception:
        return

    try:
        exif = img._getexif()
    except Exception:
        exif = None

    if not exif:
        img.close()
        return

    orientation_key = None
    for k, v in ExifTags.TAGS.items():
        if v == "Orientation":
            orientation_key = k
            break

    if orientation_key is None:
        img.close()
        return

    orientation = exif.get(orientation_key)
    if not orientation:
        img.close()
        return

    try:
        if orientation == 3:
            img = img.rotate(180, expand=True)
        elif orientation == 6:
            img = img.rotate(270, expand=True)
        elif orientation == 8:
            img = img.rotate(90, expand=True)

        img.save(image_path)
    except Exception:
        pass
    finally:
        img.close()


def ensure_vertical(image_path: Path, aspect_threshold: float = 1.2) -> None:
    """Si la imagen está muy horizontal, la rota a vertical."""
    try:
        img = Image.open(image_path)
    except Exception:
        return

    try:
        w, h = img.size
        if w > h * aspect_threshold:
            img = img.rotate(90, expand=True)
            try:
                img.save(image_path)
            except Exception:
                pass
    finally:
        img.close()


def normalize_card_image_orientation(image_path: Path) -> None:
    """Normaliza orientación de foto de carta."""
    fix_image_orientation(image_path)
    ensure_vertical(image_path)


# ---------------------------------------------------------
# Visión
# ---------------------------------------------------------
def analyze_image_with_vision(image_path: Path) -> Dict[str, Any]:
    """Llama al modelo de visión y devuelve JSON con:
       - name_detected
       - language
       - set_code
       - set_confidence
       - is_foil
       - foil_confidence
       - name_en
       - collector_number
    """
    if client is None:
        print("[ERROR] Cliente OpenAI no inicializado.")
        return {}

    # Codificamos la imagen en base64 y usamos data:URL,
    # que es el formato que el SDK acepta con type="image_url".
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")

    # Prompt mejorado
    prompt = (
        "Analiza la imagen de una carta de Magic: The Gathering y responde SOLO en JSON con este formato:\n"
        "{\n"
        '  \"name_detected\": \"Nombre impreso en la carta (respetar el idioma de la carta)\",\n'
        '  \"language\": \"Idioma de la carta (es, en, pt, etc.) basado en el texto impreso\",\n'
        '  \"set_code\": \"Código de edición (ej: IMA, 2XM, MOM). Si no estás seguro, cadena vacía.\",\n'
        '  \"set_confidence\": 0.0 a 1.0,\n'
        '  \"is_foil\": true o false,\n'
        '  \"foil_confidence\": 0.0 a 1.0,\n'
        '  \"name_en\": \"Nombre oficial en inglés tal como aparece en Scryfall/MTGJSON. Si no estás seguro, cadena vacía.\",\n'
        '  \"collector_number\": \"Número de colección que aparece en la carta (solo la parte numérica, por ejemplo \'229\'). Si no estás seguro, cadena vacía.\"\n'
        "}\n"
        "Reglas:\n"
        "- NO inventes códigos de edición: si no estás seguro, deja set_code=\"\".\n"
        "- name_detected debe ser el que aparece impreso tal cual.\n"
        "- name_en debe ser el nombre oficial en inglés si puedes identificarlo, si no déjalo vacío.\n"
        "- collector_number es el número de colección en la parte inferior de la carta; si no puedes leerlo con claridad, déjalo vacío.\n"
        "- Usa foil_confidence para indicar cuán seguro estás de que sea foil.\n"
    )

    messages = [
        {
            "role": "system",
            "content": "Eres un asistente experto en cartas de Magic: The Gathering. Respondes estrictamente en JSON válido.",
        },
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{b64}",
                        "detail": "high",
                    },
                },
            ],
        },
    ]

    for attempt in range(OPENAI_API_MAX_RETRIES):
        try:
            resp = client.chat.completions.create(
                model=OPENAI_VISION_MODEL,
                messages=messages,
                temperature=0.0,
            )
            content = resp.choices[0].message.content
            if not content:
                print("[WARN] Respuesta vacía de visión.")
                continue

            content = content.strip()
            if content.startswith("```"):
                content = content.strip("`\n ")
                if content.lower().startswith("json"):
                    content = content[4:].strip()

            data = json.loads(content)
            return data
        except Exception as e:
            print(f"[WARN] Error llamando a visión (intento {attempt+1}): {e}")
            time.sleep(OPENAI_API_RETRY_DELAY)

    print("[ERROR] No se pudo obtener respuesta válida de visión.")
    return {}



# ---------------------------------------------------------
# Utilidades nombre archivo
# ---------------------------------------------------------
def slugify_filename(s: str) -> str:
    s = s.strip().replace("/", "-")
    for bad in [":", "*", "?", '"', "<", ">", "|", "\\", "/", " "]:
        s = s.replace(bad, "_")
    return s or "1"


def get_next_available_filename(directory: Path, base_name: str) -> str:
    base = Path(base_name)
    stem = base.stem
    suffix = base.suffix

    candidate = base_name
    i = 2
    while (directory / candidate).exists():
        candidate = f"{stem} ({i}){suffix}"
        i += 1
    return candidate


def sanitize_filename_component(s: str) -> str:
    """
    Limpia un componente de nombre de archivo eliminando caracteres inválidos para Windows.
    """
    if not s:
        return s
    # Reemplazar caracteres inválidos
    s = re.sub(r'[\\/:*?"<>|]', '', s)
    # Reemplazar dobles espacios consecutivos
    s = re.sub(r'\s+', ' ', s)
    return s.strip()


# ---------------------------------------------------------
# Proceso principal
# ---------------------------------------------------------
def main():
    from config_tienda import RAW_DIR, PROCESADAS_DIR

    raw_dir = RAW_DIR
    out_path = PROCESADAS_DIR

    raw_dir.mkdir(parents=True, exist_ok=True)
    out_path.mkdir(parents=True, exist_ok=True)

    # Cargar índice existente de visión
    vision_index = load_vision_index()

    images: List[Path] = []
    for p in raw_dir.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
            continue
        if p.name.startswith("."):
            continue
        if p.stat().st_size < 10 * 1024:
            continue
        images.append(p)

    if not images:
        print("[INFO] No se encontraron imágenes en RAW.")
        return

    print(f"[INFO] Imágenes encontradas en RAW: {len(images)}")

    for idx, src in enumerate(sorted(images), start=1):
        rel_path = src.relative_to(raw_dir)
        parts = rel_path.parts
        seller_folder = parts[0] if len(parts) > 1 else None
        if seller_folder:
            seller_folder = slugify_filename(seller_folder)

        print(f"[{idx}/{len(images)}] Procesando {rel_path} ...")

        # Normalizar orientación
        normalize_card_image_orientation(src)

        # Visión
        vision_data = analyze_image_with_vision(src)
        name_detected = (vision_data.get("name_detected") or "").strip()
        name_detected = sanitize_filename_component(name_detected)
        name_detected = slugify_filename(name_detected)
        lang = (vision_data.get("language") or "").strip() or "en"
        set_code_vision = (vision_data.get("set_code") or "").strip().upper()
        set_confidence = float(vision_data.get("set_confidence") or 0.0)
        is_foil_vision = bool(vision_data.get("is_foil") or False)
        foil_confidence = float(vision_data.get("foil_confidence") or 0.0)
        name_en = (vision_data.get("name_en") or "").strip()
        collector_number = (vision_data.get("collector_number") or "").strip()

        print(
            f"      -> Visión detectó name='{name_detected}', lang={lang}, "
            f"set_code_vision={set_code_vision}, set_confidence={set_confidence}, "
            f"is_foil_vision={is_foil_vision}, foil_confidence={foil_confidence}, "
            f"name_en='{name_en}', collector_number='{collector_number}'"
        )

        # Set solo si está razonablemente seguro
        set_code_final = set_code_vision if set_code_vision and set_confidence >= 0.6 else ""

        # Foil: confiamos en visión si la seguridad es alta
        is_foil_final = is_foil_vision and (foil_confidence >= 0.7)
        condition = "NM_FOIL" if is_foil_final else "NM"

        # Nombre para el archivo (usamos el detectado, en el idioma de la carta)
        if not name_detected:
            # Fallback al nombre genérico si falla visión
            name_detected = "Carta_desconocida"
        name_slug = name_detected

        # Generamos un ID estable basado en el contenido de la imagen.
        # NO depende del nombre original del archivo.
        image_id = compute_image_id(src)
        ext = src.suffix.lower()

        if set_code_final:
            final_name = f"{name_slug} - {set_code_final} - {lang} - {condition} - {image_id}{ext}"
        else:
            # Si no tenemos set, dejamos el hueco igual que antes, pero el ID sigue siendo el hash
            final_name = f"{name_slug} -  - {lang} - {condition} - {image_id}{ext}"


        # Mantener subcarpeta de vendedor en PROCESADAS
        dst_dir = out_path / seller_folder if seller_folder else out_path
        dst_dir.mkdir(parents=True, exist_ok=True)

        final_name = get_next_available_filename(dst_dir, final_name)
        dst = dst_dir / final_name

        # Mover archivo
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.replace(dst)

        # Actualizar índice de visión con el item_id (ruta relativa en PROCESADAS)
        try:
            item_id = str(dst.relative_to(PROCESADAS_DIR)).replace("\\", "/")
        except ValueError:
            item_id = dst.name

        vision_index[item_id] = {
            "name_detected": name_detected,
            "language": lang,
            "set_code": set_code_final or set_code_vision,
            "set_confidence": set_confidence,
            "is_foil": is_foil_final,
            "foil_confidence": foil_confidence,
            "name_en": name_en,
            "collector_number": collector_number,
        }

        print(f"      -> Movido a {dst}\n")

    # Guardar índice al final
    save_vision_index(vision_index)
    print(f"[OK] Índice de visión actualizado en: {VISION_INDEX_PATH}")


if __name__ == "__main__":
    logger = get_logger("auto_etiquetar_renombrar")
    log_info("==== INICIO auto_etiquetar_renombrar ====", logger)

    try:
        main()
        log_info("==== FIN OK auto_etiquetar_renombrar ====", logger)
    except Exception as e:
        log_exception(e, logger, "auto_etiquetar_renombrar terminó con ERROR")
        # Re-lanzamos para que el .bat o quien lo llame también detecte el fallo
        raise
