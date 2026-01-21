import subprocess
import time
from pathlib import Path

from config_tienda import OUTPUT_HTML, GIT_REPO_DIR
from logger_tienda import get_logger, log_info, log_exception

BRANCH = "main"
REMOTE = "origin"


def run(cmd, cwd: Path) -> subprocess.CompletedProcess:
    """
    Ejecuta un comando en el repo y muestra la salida.
    Lanza SystemExit si el comando falla.
    """
    print(">>", " ".join(cmd))
    result = subprocess.run(
        cmd,
        cwd=str(cwd),
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        print(f"[ERROR] Comando falló con código {result.returncode}")
        raise SystemExit(result.returncode)
    return result


def main():
    html_path: Path = OUTPUT_HTML
    repo_dir: Path = GIT_REPO_DIR  # normalmente C:\Franco\Magic\tienda_web

    print("======================================")
    print("  SUBIR HTML A REPO tienda_web")
    print("======================================\n")

    print(f"[INFO] HTML generado  : {html_path}")
    print(f"[INFO] Repo HTML (git): {repo_dir}\n")

    # 1) Verificar que existan las rutas
    if not html_path.exists():
        print(f"[ERROR] No existe el archivo HTML: {html_path}")
        print("       Asegúrate de haber ejecutado actualizar_tienda.py antes.")
        raise SystemExit(1)

    if not (repo_dir / ".git").exists():
        print(f"[ERROR] La carpeta {repo_dir} no parece ser un repo git (.git no existe).")
        raise SystemExit(1)

    # 2) Ver la rama actual por sanity-check
    print("[INFO] Verificando rama actual...")
    run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_dir)

    # 3) git status antes
    print("\n[INFO] git status antes de add:")
    run(["git", "-c", "core.quotepath=false", "status", "--short"], cwd=repo_dir)

    # 4) git add index.html
    print("\n[INFO] Haciendo git add index.html ...")
    run(["git", "add", html_path.name], cwd=repo_dir)

    # 5) Ver si realmente hay algo que commitear
    print("\n[INFO] Revisando si hay cambios para commitear...")
    result = subprocess.run(
        ["git", "-c", "core.quotepath=false", "status", "--porcelain"],
        cwd=str(repo_dir),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )

    status_lines = [line for line in result.stdout.splitlines() if line.strip()]

    if not status_lines:
        print("[INFO] No hay cambios en el repo. No se hará commit ni push.")
        return

    # 6) Commit
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    commit_msg = f"Update index.html ({timestamp})"

    print("\n[INFO] Haciendo commit...")
    run(["git", "commit", "-m", commit_msg], cwd=repo_dir)

    # 7) Push
    print("\n[INFO] Haciendo push al remoto...")
    run(["git", "push", REMOTE, BRANCH], cwd=repo_dir)

    print("\n[OK] HTML subido correctamente a GitHub (repo tienda_web).")


if __name__ == "__main__":
    logger = get_logger("subir_html")
    log_info("==== INICIO subir_html ====", logger)
    try:
        main()
        log_info("==== FIN OK subir_html ====", logger)
    except Exception as e:
        log_exception(e, logger, "subir_html terminó con ERROR")
        raise

