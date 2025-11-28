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
