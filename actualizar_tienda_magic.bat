@echo off
setlocal enableextensions

REM ===============================================
REM CONFIG BASE (override con variables de entorno)
REM ===============================================
set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

if defined MAGIC_ROOT (
    set "MAGIC_ROOT=%MAGIC_ROOT%"
) else (
    for %%I in ("%SCRIPT_DIR%\..") do set "MAGIC_ROOT=%%~fI"
)

if defined INVENTARIO_DIR (
    set "INVENTARIO_DIR=%INVENTARIO_DIR%"
) else (
    set "INVENTARIO_DIR=%SCRIPT_DIR%"
)

if defined LOGDIR (
    set "LOGDIR=%LOGDIR%"
) else (
    set "LOGDIR=%MAGIC_ROOT%\Logs"
)

set "LOCKFILE=%LOGDIR%\actualizar_tienda_magic.lock"
set "BATLOG=%LOGDIR%\bat_actualizar_tienda.log"
if not defined LOCK_MAX_MINUTES set "LOCK_MAX_MINUTES=90"

if not exist "%LOGDIR%" mkdir "%LOGDIR%" 2>nul
if not exist "%LOGDIR%" (
    set "LOGDIR=%SCRIPT_DIR%\logs"
    if not exist "%LOGDIR%" mkdir "%LOGDIR%" 2>nul
)

set "LOCKFILE=%LOGDIR%\actualizar_tienda_magic.lock"
set "BATLOG=%LOGDIR%\bat_actualizar_tienda.log"

echo [INFO] LOGDIR=%LOGDIR%
echo [INFO] BATLOG=%BATLOG%

set "ERROR_FLAG=0"

REM Si ya hay lock, verificar si hay python activo; si no hay, limpiar lock huérfano
if exist "%LOCKFILE%" (
    tasklist | findstr /I "python.exe" >nul
    if errorlevel 1 (
        echo [%date% %time%] [WARN] Lockfile detectado sin python activo. Se elimina y continua.>> "%BATLOG%"
        del "%LOCKFILE%" 2>nul
    ) else (
        set "LOCK_IS_STALE=0"
        for /f %%I in ('powershell -NoProfile -Command "$f=Get-Item -LiteralPath ''%LOCKFILE%''; if(((Get-Date)-$f.LastWriteTime).TotalMinutes -ge %LOCK_MAX_MINUTES%){''1''}else{''0''}"') do set "LOCK_IS_STALE=%%I"

        if "%LOCK_IS_STALE%"=="1" (
            echo [%date% %time%] [WARN] Lockfile stale detectado (^>=%LOCK_MAX_MINUTES% min). Se elimina y continua.>> "%BATLOG%"
            del "%LOCKFILE%" 2>nul
        ) else (
            echo [%date% %time%] Ya hay una instancia en ejecucion. Saliendo...
            echo [%date% %time%] [WARN] Instancia previa detectada. Saliendo.>> "%BATLOG%"
            exit /b
        )
    )
)

REM Crear lock
echo [%date% %time%] > "%LOCKFILE%"
echo [%date% %time%] ==== INICIO actualizar_tienda_magic.bat ====>> "%BATLOG%"
echo [%date% %time%] MAGIC_ROOT=%MAGIC_ROOT%>> "%BATLOG%"
echo [%date% %time%] INVENTARIO_DIR=%INVENTARIO_DIR%>> "%BATLOG%"

cd /d "%INVENTARIO_DIR%"
if errorlevel 1 (
    echo [%date% %time%] [ERROR] No se pudo entrar a INVENTARIO_DIR=%INVENTARIO_DIR%>> "%BATLOG%"
    set "ERROR_FLAG=1"
    goto :END
)

echo ===============================================
echo   ACTUALIZANDO TIENDA MAGIC - ONE CLICK NOCTURNO
echo ===============================================

echo.
echo ---- 1) Etiquetar y renombrar cartas (VISION + PIL) ----
python auto_etiquetar_renombrar.py >> "%BATLOG%" 2>&1
if errorlevel 1 (
    set "ERROR_FLAG=1"
    echo [%date% %time%] [ERROR] auto_etiquetar_renombrar.py fallo.>> "%BATLOG%"
) else (
    echo [%date% %time%] [OK] auto_etiquetar_renombrar.py.>> "%BATLOG%"
)

echo.
echo ---- 2) Construir inventario desde fotos ----
python construir_inventario_desde_fotos.py >> "%BATLOG%" 2>&1
if errorlevel 1 (
    set "ERROR_FLAG=1"
    echo [%date% %time%] [ERROR] construir_inventario_desde_fotos.py fallo.>> "%BATLOG%"
) else (
    echo [%date% %time%] [OK] construir_inventario_desde_fotos.py.>> "%BATLOG%"
)

echo.
echo ---- 3) Actualizar precios (MTGJSON / etc.) ----
python actualizar_precios_mtgjson.py >> "%BATLOG%" 2>&1
if errorlevel 1 (
    set "ERROR_FLAG=1"
    echo [%date% %time%] [ERROR] actualizar_precios_mtgjson.py fallo.>> "%BATLOG%"
) else (
    echo [%date% %time%] [OK] actualizar_precios_mtgjson.py.>> "%BATLOG%"
)

echo.
echo ---- 4) Actualizar tienda (genera HTML y copia imagenes) ----
python actualizar_tienda.py >> "%BATLOG%" 2>&1
if errorlevel 1 (
    set "ERROR_FLAG=1"
    echo [%date% %time%] [ERROR] actualizar_tienda.py fallo.>> "%BATLOG%"
) else (
    echo [%date% %time%] [OK] actualizar_tienda.py.>> "%BATLOG%"
)

echo.
echo ---- 5) Subir HTML al repo tienda_web (subir_html.py) ----
python subir_html.py >> "%BATLOG%" 2>&1
if errorlevel 1 (
    set "ERROR_FLAG=1"
    echo [%date% %time%] [ERROR] subir_html.py fallo.>> "%BATLOG%"
) else (
    echo [%date% %time%] [OK] subir_html.py.>> "%BATLOG%"
)

echo.
echo ---- 6) Subir imágenes en lotes (subir_imagenes_por_lotes.py) ----
python subir_imagenes_por_lotes.py >> "%BATLOG%" 2>&1
if errorlevel 1 (
    set "ERROR_FLAG=1"
    echo [%date% %time%] [ERROR] subir_imagenes_por_lotes.py fallo.>> "%BATLOG%"
) else (
    echo [%date% %time%] [OK] subir_imagenes_por_lotes.py.>> "%BATLOG%"
)

:END
echo.
if "%ERROR_FLAG%"=="0" (
    echo [OK] Proceso completo. Tienda actualizada y subida en ambos repos.
    echo [%date% %time%] ==== FIN OK actualizar_tienda_magic.bat ====>> "%BATLOG%"
) else (
    echo [WARN] Proceso completo con errores. Revisar log: %BATLOG%
    echo [%date% %time%] ==== FIN CON ERRORES actualizar_tienda_magic.bat ====>> "%BATLOG%"
)

REM Borrar lock al terminar
del "%LOCKFILE%" 2>nul

endlocal & exit /b %ERROR_FLAG%
