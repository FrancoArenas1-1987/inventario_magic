# MANUAL DE USO - TIENDA MAGIC (MODO A: Procesadas como verdad)

Este manual resume cómo usar el sistema de la tienda de cartas magic
usando SOLO imágenes y el script `actualizar_tienda.py`.

La idea central es:

- La carpeta **Procesadas** es la VERDAD del stock.
- El número al final del nombre de cada imagen indica cuántas copias de esa carta hay.
- El CSV `inventario_cartas.csv` se genera automáticamente a partir de las imágenes.
- Idealmente **NO** editar el CSV para manejar stock, solo las imágenes.


## 1. Estructura de carpetas

Ruta base (ejemplo):

- `C:\Franco\Magic\inventario_magic\`
  - Scripts (`actualizar_tienda.py`, `auto_etiquetar_renombrar.py`, `construir_inventario_desde_fotos.py`, etc.)
  - `inventario_cartas.csv`
  - `MANUAL_TIENDA_MAGIC.md`

- `C:\Franco\Magic\MagicCards\`
  - `Raw\`         → fotos crudas que se van a procesar
  - `Procesadas\`  → fotos renombradas, esta carpeta define el STOCK

- `C:\Franco\Magic\tienda_web\`
  - `index.html`   → sitio web estático
  - `images\`      → copia de las imágenes para la web


## 2. Formato de los nombres de archivo en Procesadas

Cada imagen en **Procesadas** debe seguir este patrón:

`Nombre de Carta - SET - lang - COND[_FOIL] - N.ext`

Ejemplos:

- `Lightning Bolt - M11 - en - NM - 4.jpg`
- `Lightning Bolt - M11 - en - NM_FOIL - 2.jpg`
- `Llanowar Elves - 10E - es - EX - 3.png`

Donde:

- `SET` = código de edición Scryfall (m11, 10e, etc).
- `lang` = idioma (en, es, pt, etc).
- `COND` = condición (NM, EX, SP, MP, HP).
- `_FOIL` = se agrega si la carta es foil.
- `N` = **cantidad de copias** que representa esa foto.

> IMPORTANTE: El sistema siempre calcula la cantidad (quantity) a partir de este `N`.


## 3. Flujo diario: agregar NUEVAS cartas (compras)

### 3.1. Paso a paso

1. Saca fotos de las nuevas cartas y colócalas en la carpeta:

   `MagicCards\Raw\`

2. Ejecuta en terminal:

   ```bash
   cd C:\Franco\Magic\inventario_magic
   python actualizar_tienda.py
   ```


### 3.2. Flujo recomendado para que se vea en GitHub Pages

Si agregaste fotos nuevas en `Raw`, usa el flujo completo para evitar que falten
metadatos o precios:

```bash
cd C:\Franco\Magic\inventario_magic
python auto_etiquetar_renombrar.py
python construir_inventario_desde_fotos.py
python actualizar_precios_mtgjson.py
python actualizar_tienda.py
python subir_html.py
python subir_imagenes_por_lotes.py
```

También puedes correr todo de una vez:

```bat
actualizar_tienda_magic.bat
```


## 4. Qué hace cada paso (resumen práctico)

1. `auto_etiquetar_renombrar.py`
   - Lee fotos en `MagicCards\Raw`.
   - Usa visión para detectar carta/idioma/set.
   - Mueve a `MagicCards\Procesadas` con nombre normalizado.

2. `construir_inventario_desde_fotos.py`
   - Crea/actualiza inventarios por vendedor en `inventarios_vendedores/*.csv`.

3. `actualizar_precios_mtgjson.py`
   - Busca precios de referencia y rellena `price_clp`.

4. `actualizar_tienda.py`
   - Regenera completo `tienda_web/index.html`.
   - Copia imágenes procesadas al repo de imágenes (`tienda_web_images/images`).

5. `subir_html.py`
   - Hace `git add/commit/push` del `index.html` del repo `tienda_web`.

6. `subir_imagenes_por_lotes.py`
   - Hace commit/push por lotes del repo `tienda_web_images`.


## 5. Páginas para monitorear el proceso

Revisa estas páginas en este orden:

1. **Repo HTML** (confirmar commit de `index.html`):
   - `https://github.com/FrancoArenas1-1987/tienda_web`

2. **Actions del repo HTML** (si Pages o deploy usa workflows):
   - `https://github.com/FrancoArenas1-1987/tienda_web/actions`

3. **Configuración de GitHub Pages** del repo HTML:
   - `https://github.com/FrancoArenas1-1987/tienda_web/settings/pages`

4. **Sitio publicado** (resultado final):
   - URL de GitHub Pages configurada para `tienda_web`

5. **Repo de imágenes** (confirmar lotes subidos):
   - `https://github.com/FrancoArenas1-1987/tienda_web_images`

6. **Actions del repo de imágenes** (si aplica):
   - `https://github.com/FrancoArenas1-1987/tienda_web_images/actions`


## 6. Checklist rápido de validación

- Verificar que en consola aparezca `[OK]` en cada script.
- Confirmar commit nuevo en `tienda_web` con cambio en `index.html`.
- Confirmar commits nuevos en `tienda_web_images` (lotes).
- Abrir la URL pública y forzar recarga (`Ctrl + F5`).
- Si no aparece la carta, revisar:
  - nombre de archivo en `Procesadas`,
  - `status=Disponible`,
  - `quantity > 0`,
  - que imagen y HTML estén realmente pusheados.
