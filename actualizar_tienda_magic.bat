@echo off
REM ============================================================
REM  One-click: Actualizar tienda Magic (cartas, precios y web)
REM  Ejecuta:
REM   - auto_etiquetar_renombrar.py
REM   - construir_inventario_desde_fotos.py
REM   - actualizar_tienda.py (genera HTML, copia imágenes y hace git push)
REM ============================================================

REM Ir a la carpeta donde está este .bat (inventario_magic)
cd C:\Franco\Magic\inventario_magic "%~dp0"

echo.
echo ============================================================
echo   ACTUALIZANDO TIENDA MAGIC - INVENTARIO Y SITIO WEB
echo ============================================================
echo  Carpeta actual: %CD%
echo.

REM --- (Opcional) Activar entorno virtual de Python ---
REM Si usas venv, descomenta estas dos líneas y ajusta la ruta:
REM echo Activando entorno virtual...
REM call "%CD%\venv\Scripts\activate.bat"

REM --- Verificar que Python esté disponible ---
python --version >nul 2>&1
IF ERRORLEVEL 1 (
    echo [ERROR] No se encontró "python" en el PATH.
    echo Asegurate de tener Python instalado y agregado al PATH.
    echo.
    pause
    exit /b 1
)

echo.
echo [INFO] Iniciando flujo completo con actualizar_tienda.py
echo     (esto procesara fotos, generara inventario, HTML y hara git push)
echo.

REM --- Ejecutar el orquestador principal ---
python actualizar_tienda.py
IF ERRORLEVEL 1 (
    echo.
    echo [ERROR] Ocurrió un problema al ejecutar actualizar_tienda.py
    echo Revisa los mensajes anteriores para ver en qué paso falló.
    echo.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   PROCESO COMPLETO FINALIZADO SIN ERRORES
echo ============================================================
echo  - Inventario actualizado (inventario_cartas.csv)
echo  - Sitio generado en carpeta ..\tienda_web
echo  - Cambios enviados al repositorio Git (GitHub Pages)
echo.
pause
exit /b 0
