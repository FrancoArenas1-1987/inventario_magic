@echo off
echo ===============================================
echo   ACTUALIZANDO TIENDA MAGIC - ONE CLICK
echo ===============================================

cd /d %~dp0

echo ---- 1) Etiquetar y renombrar ----
python auto_etiquetar_renombrar.py

echo ---- 2) Construir inventario ----
python construir_inventario_desde_fotos.py

echo ---- 3) Actualizar precios (MTGJSON + Scryfall) ----
python actualizar_precios_mtgjson.py

echo ---- 4) Generar tienda ----
python actualizar_tienda.py

echo ---- Proceso completado ----
pause
