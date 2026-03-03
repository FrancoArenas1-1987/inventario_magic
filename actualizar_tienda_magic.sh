#!/bin/bash

#===============================================
# CONFIG BASE (override con variables de entorno)
#===============================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Configurar MAGIC_ROOT (padre del inventario_magic)
if [ -z "$MAGIC_ROOT" ]; then
    MAGIC_ROOT="$(dirname "$SCRIPT_DIR")"
fi

# Configurar INVENTARIO_DIR
if [ -z "$INVENTARIO_DIR" ]; then
    INVENTARIO_DIR="$SCRIPT_DIR"
fi

# Configurar LOGDIR
if [ -z "$LOGDIR" ]; then
    LOGDIR="$MAGIC_ROOT/Logs"
fi

# Establecer timeout máximo para lockfile (en minutos)
LOCK_MAX_MINUTES=${LOCK_MAX_MINUTES:-90}

# Rutas de lock y log
LOCKFILE="$LOGDIR/actualizar_tienda_magic.lock"
BATLOG="$LOGDIR/bash_actualizar_tienda.log"

# Crear directorio de logs si no existe
mkdir -p "$LOGDIR" 2>/dev/null || {
    LOGDIR="$SCRIPT_DIR/logs"
    mkdir -p "$LOGDIR" 2>/dev/null
}

LOCKFILE="$LOGDIR/actualizar_tienda_magic.lock"
BATLOG="$LOGDIR/bash_actualizar_tienda.log"

echo "[INFO] LOGDIR=$LOGDIR"
echo "[INFO] BATLOG=$BATLOG"

ERROR_FLAG=0

#===============================================
# LOCK FILE MANAGEMENT
#===============================================

# Si ya hay lockfile, verificar si es válido o está obsoleto
if [ -f "$LOCKFILE" ]; then
    LOCK_AGE_MINUTES=$(( ($(date +%s) - $(stat -f%m "$LOCKFILE" 2>/dev/null || stat -c%Y "$LOCKFILE" 2>/dev/null)) / 60 ))
    
    # Verificar si hay procesos Python activos
    if pgrep -f "python|python3" > /dev/null; then
        # Hay Python activo, verificar si el lock está obsoleto
        if [ "$LOCK_AGE_MINUTES" -ge "$LOCK_MAX_MINUTES" ]; then
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] [WARN] Lockfile stale detectado (>=$LOCK_MAX_MINUTES min). Se elimina y continua." >> "$BATLOG"
            rm -f "$LOCKFILE"
        else
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] Ya hay una instancia en ejecución. Saliendo..."
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] [WARN] Instancia previa detectada. Saliendo." >> "$BATLOG"
            exit 1
        fi
    else
        # No hay Python activo, el lockfile es huérfano
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] [WARN] Lockfile detectado sin python activo. Se elimina y continua." >> "$BATLOG"
        rm -f "$LOCKFILE"
    fi
fi

# Crear lock
echo "[$(date '+%Y-%m-%d %H:%M:%S')]" > "$LOCKFILE"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] ==== INICIO actualizar_tienda_magic.sh ====" >> "$BATLOG"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] MAGIC_ROOT=$MAGIC_ROOT" >> "$BATLOG"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] INVENTARIO_DIR=$INVENTARIO_DIR" >> "$BATLOG"

# Entrar al directorio de inventario
cd "$INVENTARIO_DIR" || {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [ERROR] No se pudo entrar a INVENTARIO_DIR=$INVENTARIO_DIR" >> "$BATLOG"
    ERROR_FLAG=1
    rm -f "$LOCKFILE"
    exit 1
}

echo "==============================================="
echo "  ACTUALIZANDO TIENDA MAGIC - ONE CLICK NOCTURNO"
echo "==============================================="

#===============================================
# EJECUTAR SCRIPTS
#===============================================

echo ""
echo "---- 1) Etiquetar y renombrar cartas (VISION + PIL) ----"
python3 auto_etiquetar_renombrar.py >> "$BATLOG" 2>&1
if [ $? -ne 0 ]; then
    ERROR_FLAG=1
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [ERROR] auto_etiquetar_renombrar.py fallo." >> "$BATLOG"
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [OK] auto_etiquetar_renombrar.py." >> "$BATLOG"
fi

echo ""
echo "---- 2) Construir inventario desde fotos ----"
python3 construir_inventario_desde_fotos.py >> "$BATLOG" 2>&1
if [ $? -ne 0 ]; then
    ERROR_FLAG=1
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [ERROR] construir_inventario_desde_fotos.py fallo." >> "$BATLOG"
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [OK] construir_inventario_desde_fotos.py." >> "$BATLOG"
fi

echo ""
echo "---- 3) Actualizar precios (MTGJSON / etc.) ----"
python3 actualizar_precios_mtgjson.py >> "$BATLOG" 2>&1
if [ $? -ne 0 ]; then
    ERROR_FLAG=1
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [ERROR] actualizar_precios_mtgjson.py fallo." >> "$BATLOG"
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [OK] actualizar_precios_mtgjson.py." >> "$BATLOG"
fi

echo ""
echo "---- 4) Actualizar tienda (genera HTML y copia imagenes) ----"
python3 actualizar_tienda.py >> "$BATLOG" 2>&1
if [ $? -ne 0 ]; then
    ERROR_FLAG=1
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [ERROR] actualizar_tienda.py fallo." >> "$BATLOG"
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [OK] actualizar_tienda.py." >> "$BATLOG"
fi

echo ""
echo "---- 5) Subir HTML al repo tienda_web (subir_html.py) ----"
python3 subir_html.py >> "$BATLOG" 2>&1
if [ $? -ne 0 ]; then
    ERROR_FLAG=1
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [ERROR] subir_html.py fallo." >> "$BATLOG"
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [OK] subir_html.py." >> "$BATLOG"
fi

echo ""
echo "---- 6) Subir imágenes en lotes (subir_imagenes_por_lotes.py) ----"
python3 subir_imagenes_por_lotes.py >> "$BATLOG" 2>&1
if [ $? -ne 0 ]; then
    ERROR_FLAG=1
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [ERROR] subir_imagenes_por_lotes.py fallo." >> "$BATLOG"
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [OK] subir_imagenes_por_lotes.py." >> "$BATLOG"
fi

#===============================================
# RESUMEN FINAL Y CLEANUP
#===============================================

echo ""
if [ "$ERROR_FLAG" -eq 0 ]; then
    echo "[OK] Proceso completo. Tienda actualizada y subida en ambos repos."
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ==== FIN OK actualizar_tienda_magic.sh ====" >> "$BATLOG"
else
    echo "[WARN] Proceso completo con errores. Revisar log: $BATLOG"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ==== FIN CON ERRORES actualizar_tienda_magic.sh ====" >> "$BATLOG"
fi

# Borrar lock al terminar
rm -f "$LOCKFILE"

exit "$ERROR_FLAG"
