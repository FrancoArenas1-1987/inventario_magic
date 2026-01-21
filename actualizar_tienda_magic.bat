@echo off
setlocal

set "LOCKFILE=C:\Franco\Magic\actualizar_tienda_magic.lock"
set "LOGDIR=C:\Franco\Magic\inventario_magic\logs"
set "BATLOG=%LOGDIR%\bat_actualizar_tienda.log"

if not exist "%LOGDIR%" mkdir "%LOGDIR%"

set "ERROR_FLAG=0"

REM Si ya hay un proceso corriendo, salir
if exist "%LOCKFILE%" (
    echo [%date% %time%] Ya hay una instancia en ejecucion. Saliendo...
    echo [%date% %time%] [WARN] Instancia previa detectada. Saliendo.>> "%BATLOG%"
    exit /b
)

REM Crear lock
echo [%date% %time%] > "%LOCKFILE%"
echo [%date% %time%] ==== INICIO actualizar_tienda_magic.bat ====>> "%BATLOG%"

echo ===============================================
echo   ACTUALIZANDO TIENDA MAGIC - ONE CLICK NOCTURNO
echo ===============================================

REM -----------------------------------------------
REM 1) Activar entorno virtual (SI LO USAS)
REM    Si usas venv, descomenta la línea de abajo y pon tu ruta real
REM -----------------------------------------------
REM call C:\Franco\Magic\venv\Scripts\activate.bat

REM -----------------------------------------------
REM 2) Ir al proyecto de inventario y procesar TODO
REM -----------------------------------------------
cd /d C:\Franco\Magic\inventario_magic

echo.
echo ---- 1) Etiquetar y renombrar cartas (VISION + PIL) ----
python auto_etiquetar_renombrar.py
if errorlevel 1 (
    set "ERROR_FLAG=1"
    echo [%date% %time%] [ERROR] auto_etiquetar_renombrar.py fallo.>> "%BATLOG%"
) else (
    echo [%date% %time%] [OK] auto_etiquetar_renombrar.py.>> "%BATLOG%"
)

echo.
echo ---- 2) Construir inventario desde fotos ----
python construir_inventario_desde_fotos.py
if errorlevel 1 (
    set "ERROR_FLAG=1"
    echo [%date% %time%] [ERROR] construir_inventario_desde_fotos.py fallo.>> "%BATLOG%"
) else (
    echo [%date% %time%] [OK] construir_inventario_desde_fotos.py.>> "%BATLOG%"
)

echo.
echo ---- 3) Actualizar precios (MTGJSON / etc.) ----
python actualizar_precios_mtgjson.py
if errorlevel 1 (
    set "ERROR_FLAG=1"
    echo [%date% %time%] [ERROR] actualizar_precios_mtgjson.py fallo.>> "%BATLOG%"
) else (
    echo [%date% %time%] [OK] actualizar_precios_mtgjson.py.>> "%BATLOG%"
)

echo.
echo ---- 4) Actualizar tienda (genera HTML y copia imagenes) ----
python actualizar_tienda.py
if errorlevel 1 (
    set "ERROR_FLAG=1"
    echo [%date% %time%] [ERROR] actualizar_tienda.py fallo.>> "%BATLOG%"
) else (
    echo [%date% %time%] [OK] actualizar_tienda.py.>> "%BATLOG%"
)
REM -----------------------------------------------
REM 5) Subir SOLO el HTML al repo tienda_web
REM -----------------------------------------------
cd /d C:\Franco\Magic\inventario_magic

echo.
echo ---- 5) Subir HTML al repo tienda_web (subir_html.py) ----
python subir_html.py
if errorlevel 1 (
    set "ERROR_FLAG=1"
    echo [%date% %time%] [ERROR] subir_html.py fallo.>> "%BATLOG%"
) else (
    echo [%date% %time%] [OK] subir_html.py.>> "%BATLOG%"
)

echo.
echo ---- 6) Subir imágenes en lotes (subir_imagenes_por_lotes.py) ----
python subir_imagenes_por_lotes.py
if errorlevel 1 (
    set "ERROR_FLAG=1"
    echo [%date% %time%] [ERROR] subir_imagenes_por_lotes.py fallo.>> "%BATLOG%"
) else (
    echo [%date% %time%] [OK] subir_imagenes_por_lotes.py.>> "%BATLOG%"
)

echo.
if "%ERROR_FLAG%"=="0" (
    echo [OK] Proceso completo. Tienda actualizada y subida en ambos repos.
    echo [%date% %time%] ==== FIN OK actualizar_tienda_magic.bat ====>> "%BATLOG%"
) else (
    echo [WARN] Proceso completo con errores. Revisar log.
    echo [%date% %time%] ==== FIN CON ERRORES actualizar_tienda_magic.bat ====>> "%BATLOG%"
)

REM Borrar lock al terminar bien
del "%LOCKFILE%" 2>nul

REM Salir con código de error si hubo problemas
endlocal & exit /b %ERROR_FLAG%
