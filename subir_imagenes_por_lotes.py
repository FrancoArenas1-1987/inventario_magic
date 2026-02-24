import subprocess
import os
import time
from pathlib import Path
from typing import List
from logger_tienda import get_logger, log_info, log_exception
from config_tienda import IMAGES_REPO_DIR, IMAGES_REPO_IMAGES_DIR

# Ruta del repo de IMÁGENES
REPO_DIR = str(IMAGES_REPO_DIR)

# Carpeta dentro del repo donde están las imágenes
IMAGES_DIR = IMAGES_REPO_IMAGES_DIR.name

# Cantidad de archivos por lote (ajusta si quieres)
BATCH_SIZE = 150

BRANCH = "main"
REMOTE = "origin"

# Pausa entre pushes, en segundos (para no pegarle tan fuerte a GitHub)
SLEEP_BETWEEN_BATCHES = 20


def run(cmd: List[str], capture_output: bool = False) -> subprocess.CompletedProcess:
    """
    Ejecuta un comando en el repo y muestra la salida.
    Si capture_output=True, devuelve el CompletedProcess con stdout/stderr.
    """
    print(">>", " ".join(cmd))
    result = subprocess.run(
        cmd,
        cwd=REPO_DIR,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=capture_output,
    )
    if result.returncode != 0:
        print(f"[ERROR] Comando falló con código {result.returncode}")
        if result.stdout:
            print("STDOUT:", result.stdout)
        if result.stderr:
            print("STDERR:", result.stderr)
        raise SystemExit(result.returncode)
    return result


def get_current_branch() -> str:
    result = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True)
    return (result.stdout or "").strip()


def listar_imagenes() -> List[str]:
    """
    Lista TODOS los archivos dentro de IMAGES_DIR (recursivo) como rutas relativas al repo.
    No depende de git status, recorre el sistema de archivos.
    """
    base = Path(REPO_DIR) / IMAGES_DIR
    if not base.is_dir():
        print(f"[ERROR] La carpeta {base} no existe.")
        raise SystemExit(1)

    rutas: List[str] = []
    for p in base.rglob("*"):
        if p.is_file():
            # ruta relativa al repo
            rel = p.relative_to(REPO_DIR)
            rutas.append(str(rel).replace(os.sep, "/"))

    rutas.sort()
    return rutas


def hay_cambios_staged() -> bool:
    """
    Devuelve True si hay cambios staged (listos para commit).
    """
    result = run(
        ["git", "diff", "--cached", "--quiet"],
        capture_output=True,
    )
    # diff --quiet devuelve:
    #   0 si NO hay cambios
    #   1 si SÍ hay cambios
    # Otros códigos ya los maneja run()
    return result.returncode == 1  # pero run() ya habría hecho exit si !=0 o !=1


def main():
    print(f"[INFO] Repo dir: {REPO_DIR}")
    print(f"[INFO] Carpeta de imágenes: {IMAGES_DIR}")

    if not (Path(REPO_DIR) / ".git").exists():
        print(f"[ERROR] {REPO_DIR} no parece repositorio git (.git no existe).")
        raise SystemExit(1)

    os.chdir(REPO_DIR)

    current_branch = get_current_branch()
    print(f"[INFO] Rama actual: {current_branch}")
    if current_branch != BRANCH:
        print(f"[WARN] No estás en la rama {BRANCH}. Estás en {current_branch}.")

    todas = listar_imagenes()
    total = len(todas)
    print(f"[INFO] Imágenes encontradas en {IMAGES_DIR}: {total}")

    if not total:
        print("[INFO] No se encontraron imágenes para subir.")
        return

    batch_num = 0
    for i in range(0, total, BATCH_SIZE):
        batch_num += 1
        batch = todas[i : i + BATCH_SIZE]

        print("\n======================================")
        print(f"[INFO] Lote {batch_num}: {len(batch)} archivos")
        print("======================================")
        for f in batch:
            print("   -", f)

        # git add de este lote
        run(["git", "add"] + batch)

        # Verificar si realmente hay algo staged
        # (por si el script se ejecuta de nuevo y ya están trackeadas)
        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=REPO_DIR,
        )
        if result.returncode == 0:
            print("[INFO] No hay cambios staged después del add. Se salta este lote.")
            continue
        elif result.returncode not in (0, 1):
            print("[ERROR] git diff --cached --quiet falló.")
            raise SystemExit(result.returncode)

        # Commit
        msg = f"Add image batch {batch_num}"
        run(["git", "commit", "-m", msg])

        # Push
        run(["git", "push", REMOTE, BRANCH])

        if i + BATCH_SIZE < total:
            print(
                f"[INFO] Lote {batch_num} subido. Esperando "
                f"{SLEEP_BETWEEN_BATCHES} segundos antes del siguiente..."
            )
            time.sleep(SLEEP_BETWEEN_BATCHES)

    print("\n[OK] Todos los lotes de imágenes fueron subidos.")


if __name__ == "__main__":
    logger = get_logger("subir_imagenes_por_lotes_runner")
    try:
        main()
    except Exception as e:
        log_exception(e, logger, "subir_imagenes_por_lotes terminó con ERROR")
        raise

