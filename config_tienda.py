"""
Configuración centralizada para la tienda de cartas Magic.

La idea es que TODOS los scripts (auto_etiquetar_renombrar.py,
construir_inventario_desde_fotos.py, actualizar_tienda.py, etc.)
usen estas rutas, así solo se modifica este archivo si cambia
la estructura de carpetas.

Estructura asumida (relativa a este archivo):

    inventario_magic/           ← proyecto con los scripts
    tienda_web/                 ← carpeta donde se genera la web estática
        index.html
        images/
    MagicCards/
        Raw/                    ← fotos originales
        Procesadas/             ← fotos ya renombradas

Si tu estructura es distinta, ajusta los nombres de carpeta más abajo.
"""

from pathlib import Path

# =========================
#  BASES DE RUTA
# =========================

# Carpeta donde está este archivo (inventario_magic/)
PROJECT_ROOT: Path = Path(__file__).resolve().parent

# Carpeta padre (normalmente .../Magic/)
PROJECT_PARENT: Path = PROJECT_ROOT.parent

# =========================
#  CARPETAS DE IMÁGENES
# =========================

# Carpeta con las fotos crudas que vienen de la cámara/escaner
RAW_DIR: Path = PROJECT_PARENT / "MagicCards" / "Raw"

# Carpeta con las fotos ya procesadas y renombradas por auto_etiquetar_renombrar.py
PROCESADAS_DIR: Path = PROJECT_PARENT / "MagicCards" / "Procesadas"

# =========================
#  INVENTARIO (CSV)
# =========================

# Inventario principal de cartas
INVENTORY_CSV: Path = PROJECT_ROOT / "inventario_cartas.csv"

# CSV donde se registran problemas / errores de construcción de inventario
INVENTORY_ERRORES_CSV: Path = PROJECT_ROOT / "inventario_cartas_errores.csv"

# =========================
#  SALIDA WEB / DEPLOY
# =========================

# Carpeta donde vive la web estática (la que se sube a OneDrive / GitHub Pages / IIS, etc.)
DEPLOY_DIR: Path = PROJECT_PARENT / "tienda_web"

# HTML final que ve el usuario (normalmente index.html en la carpeta de deploy)
OUTPUT_HTML: Path = DEPLOY_DIR / "index.html"

# Carpeta donde se copian las imágenes para la web
DEPLOY_IMAGES_DIR: Path = DEPLOY_DIR / "images"

# =========================
#  REPO GIT
# =========================

# Carpeta del repositorio git donde se hará `git add/commit/push`.
# En tu caso normalmente será la misma que DEPLOY_DIR, pero si
# cambias la estructura, puedes apuntar a otra carpeta.
GIT_REPO_DIR: Path = DEPLOY_DIR


# =========================
#  UTILIDADES
# =========================

def ensure_directories() -> None:
    """
    Crea (si no existen) las carpetas necesarias para que la app funcione.

    Se puede llamar al inicio de scripts como:
        from config_tienda import ensure_directories
        ensure_directories()
    """
    for path in [RAW_DIR, PROCESADAS_DIR, DEPLOY_DIR, DEPLOY_IMAGES_DIR]:
        path.mkdir(parents=True, exist_ok=True)


if __name__ == "__main__":
    # Pequeño test manual para verificar las rutas
    print("PROJECT_ROOT        :", PROJECT_ROOT)
    print("PROJECT_PARENT      :", PROJECT_PARENT)
    print("RAW_DIR             :", RAW_DIR)
    print("PROCESADAS_DIR      :", PROCESADAS_DIR)
    print("INVENTORY_CSV       :", INVENTORY_CSV)
    print("INVENTORY_ERRORES_CSV:", INVENTORY_ERRORES_CSV)
    print("DEPLOY_DIR          :", DEPLOY_DIR)
    print("OUTPUT_HTML         :", OUTPUT_HTML)
    print("DEPLOY_IMAGES_DIR   :", DEPLOY_IMAGES_DIR)
    print("GIT_REPO_DIR        :", GIT_REPO_DIR)

    ensure_directories()
    print("\n[OK] Directorios verificados/creados.")
