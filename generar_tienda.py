import csv
from pathlib import Path

# ======= CONFIGURACIÓN =======

INVENTORY_CSV = "inventario_cartas.csv"
OUTPUT_HTML = "tienda_magic.html"

# Desde la carpeta del proyecto (inventario_magic) a la carpeta de imágenes
# C:\Users\franc\OneDrive\Magic\inventario_magic
# C:\Users\franc\OneDrive\Magic\MagicCards\Procesadas
IMAGE_RELATIVE_PATH = "../MagicCards/Procesadas"


def format_clp(value: str) -> str:
    """
    Recibe un string con número (ej: "1500") y lo devuelve formateado
    con puntos de miles: "1.500". Si está vacío, devuelve "Consultar".
    """
    if not value:
        return "Consultar"
    try:
        n = int(value)
    except ValueError:
        return value
    return f"{n:,}".replace(",", ".")


def load_inventory(path: Path):
    rows = []
    if not path.exists():
        raise SystemExit(f"No se encontró el archivo de inventario: {path}")

    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Solo mostramos cartas disponibles
            status = (row.get("status") or "").lower()
            if status and status != "available":
                continue
            rows.append(row)

    # Ordenamos por nombre
    rows.sort(key=lambda r: r.get("name", "").lower())
    return rows


def build_card_html(row: dict) -> str:
    name = row.get("name", "Sin nombre")
    set_code = (row.get("set") or "").upper()
    lang = (row.get("lang") or "").upper()
    condition = (row.get("condition") or "").upper()
    fmt = row.get("format") or ""
    qty = row.get("quantity") or "1"
    price_clp = format_clp(row.get("price_clp", ""))

    image_file = row.get("image_url", "")
    image_src = f"{IMAGE_RELATIVE_PATH}/{image_file}".replace("\\", "/")

    # Para el buscador
    data_name = name.lower()
    data_set = set_code.lower()
    data_lang = lang.lower()
    data_cond = condition.lower()

    tags_parts = []
    if set_code:
        tags_parts.append(set_code)
    if lang:
        tags_parts.append(lang)
    if condition:
        tags_parts.append(condition)
    if fmt:
        tags_parts.append(fmt)
    tags_text = " • ".join(tags_parts)

    card_html = f"""
      <div class="card"
           data-name="{data_name}"
           data-set="{data_set}"
           data-lang="{data_lang}"
           data-cond="{data_cond}">
        <div class="card-image">
          <img src="{image_src}" alt="{name}">
        </div>
        <div class="card-body">
          <div class="card-title">{name}</div>
          <div class="card-tags">{tags_text}</div>
          <div class="card-price">$ {price_clp} CLP</div>
          <div class="card-qty">Cantidad: {qty}</div>
        </div>
      </div>
    """
    return card_html


def build_html(cards_html: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <title>Tienda de Cartas Magic</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    :root {{
      --bg: #0b0b10;
      --bg-card: #181824;
      --bg-card-hover: #232336;
      --border-card: #2a2a3d;
      --accent: #7b5cff;
      --accent-soft: rgba(123, 92, 255, 0.15);
      --text-main: #f5f5f7;
      --text-muted: #b0b0bc;
      --danger: #ff6b6b;
    }}

    * {{
      box-sizing: border-box;
    }}

    body {{
      margin: 0;
      padding: 0;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: radial-gradient(circle at top, #181824 0, #050509 55%, #000 100%);
      color: var(--text-main);
      min-height: 100vh;
    }}

    .page {{
      max-width: 1200px;
      margin: 0 auto;
      padding: 24px 16px 40px;
    }}

    header {{
      margin-bottom: 24px;
    }}

    .title {{
      font-size: 1.8rem;
      font-weight: 700;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      display: flex;
      align-items: center;
      gap: 8px;
    }}

    .title-badge {{
      font-size: 0.78rem;
      padding: 2px 8px;
      border-radius: 999px;
      background: var(--accent-soft);
      color: var(--accent);
      border: 1px solid rgba(123, 92, 255, 0.4);
    }}

    .subtitle {{
      margin-top: 6px;
      color: var(--text-muted);
      font-size: 0.95rem;
    }}

    .toolbar {{
      margin-top: 18px;
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      align-items: center;
      justify-content: space-between;
    }}

    .search-box {{
      position: relative;
      flex: 1 1 260px;
      max-width: 420px;
    }}

    .search-box input {{
      width: 100%;
      padding: 10px 12px 10px 32px;
      border-radius: 999px;
      border: 1px solid rgba(255, 255, 255, 0.06);
      background: rgba(10, 10, 18, 0.9);
      color: var(--text-main);
      font-size: 0.92rem;
      outline: none;
      box-shadow: 0 0 0 1px transparent;
      transition: border 0.15s ease, box-shadow 0.15s ease, background 0.15s;
    }}

    .search-box input::placeholder {{
      color: #6d6d7a;
    }}

    .search-box input:focus {{
      border-color: rgba(123, 92, 255, 0.8);
      box-shadow: 0 0 0 1px rgba(123, 92, 255, 0.5);
      background: rgba(10, 10, 22, 0.98);
    }}

    .search-icon {{
      position: absolute;
      left: 10px;
      top: 50%;
      transform: translateY(-50%);
      font-size: 0.9rem;
      color: #7e7e8c;
    }}

    .filters {{
      display: flex;
      gap: 8px;
      align-items: center;
      flex-wrap: wrap;
      font-size: 0.85rem;
      color: var(--text-muted);
    }}

    .filters select {{
      background: rgba(10, 10, 18, 0.9);
      border-radius: 999px;
      border: 1px solid rgba(255, 255, 255, 0.06);
      color: var(--text-main);
      padding: 6px 10px;
      font-size: 0.85rem;
      outline: none;
    }}

    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
      gap: 16px;
      margin-top: 20px;
    }}

    .card {{
      background: linear-gradient(145deg, #11111b 0%, #171727 100%);
      border-radius: 14px;
      border: 1px solid var(--border-card);
      overflow: hidden;
      display: flex;
      flex-direction: column;
      box-shadow:
        0 18px 30px rgba(0,0,0,0.65),
        0 0 0 1px rgba(255,255,255,0.01);
      transform: translateY(0);
      transition: transform 0.18s ease, box-shadow 0.18s ease, border 0.18s ease, background 0.18s ease;
    }}

    .card:hover {{
      transform: translateY(-4px);
      border-color: rgba(123, 92, 255, 0.9);
      background: radial-gradient(circle at top, #20203b 0, #141425 55%, #10101b 100%);
      box-shadow:
        0 22px 40px rgba(0,0,0,0.8),
        0 0 0 1px rgba(123,92,255,0.25);
    }}

    .card-image {{
      aspect-ratio: 3 / 4;
      background: radial-gradient(circle at top, #26263b 0, #11111a 55%, #040407 100%);
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 10px;
    }}

    .card-image img {{
      max-width: 100%;
      max-height: 100%;
      border-radius: 10px;
      display: block;
      object-fit: contain;
      box-shadow:
        0 12px 18px rgba(0,0,0,0.8),
        0 0 0 1px rgba(0,0,0,0.6);
    }}

    .card-body {{
      padding: 10px 11px 11px;
      display: flex;
      flex-direction: column;
      gap: 4px;
    }}

    .card-title {{
      font-size: 0.95rem;
      font-weight: 600;
      line-height: 1.3;
    }}

    .card-tags {{
      font-size: 0.78rem;
      color: var(--text-muted);
    }}

    .card-price {{
      font-size: 0.95rem;
      font-weight: 600;
      margin-top: 4px;
      color: var(--accent);
    }}

    .card-qty {{
      font-size: 0.8rem;
      color: var(--text-muted);
    }}

    .empty-state {{
      margin-top: 40px;
      text-align: center;
      color: var(--text-muted);
      font-size: 0.95rem;
    }}

    .hidden {{
      display: none !important;
    }}

    @media (max-width: 600px) {{
      .page {{
        padding: 16px 10px 30px;
      }}
      .title {{
        font-size: 1.4rem;
      }}
      .grid {{
        gap: 12px;
      }}
    }}
  </style>
</head>
<body>
  <div class="page">
    <header>
      <div class="title">
        Tienda Magic
        <span class="title-badge">Inventario automático</span>
      </div>
      <div class="subtitle">
        Busca por nombre, edición, idioma o filtra por idioma. Solo se muestran cartas disponibles.
      </div>
      <div class="toolbar">
        <div class="search-box">
          <span class="search-icon">🔍</span>
          <input id="searchInput" type="text" placeholder="Buscar por nombre, set o idioma...">
        </div>
        <div class="filters">
          <span>Idioma:</span>
          <select id="langFilter">
            <option value="">Todos</option>
            <option value="es">ES</option>
            <option value="en">EN</option>
          </select>
        </div>
      </div>
    </header>

    <main>
      <div id="cardsGrid" class="grid">
        {cards_html}
      </div>
      <div id="emptyState" class="empty-state hidden">
        No se encontraron cartas con los filtros actuales.
      </div>
    </main>
  </div>

  <script>
    const searchInput = document.getElementById('searchInput');
    const langFilter = document.getElementById('langFilter');
    const cards = Array.from(document.querySelectorAll('.card'));
    const emptyState = document.getElementById('emptyState');

    function applyFilters() {{
      const term = searchInput.value.toLowerCase().trim();
      const lang = langFilter.value.toLowerCase().trim();

      let visibleCount = 0;

      cards.forEach(card => {{
        const name = card.dataset.name || "";
        const set = card.dataset.set || "";
        const cardLang = card.dataset.lang || "";

        const textMatch = !term || (name.includes(term) || set.includes(term) || cardLang.includes(term));
        const langMatch = !lang || cardLang === lang;

        if (textMatch && langMatch) {{
          card.classList.remove('hidden');
          visibleCount += 1;
        }} else {{
          card.classList.add('hidden');
        }}
      }});

      if (visibleCount === 0) {{
        emptyState.classList.remove('hidden');
      }} else {{
        emptyState.classList.add('hidden');
      }}
    }}

    searchInput.addEventListener('input', applyFilters);
    langFilter.addEventListener('change', applyFilters);
  </script>
</body>
</html>
"""


def main():
    inventory_path = Path(INVENTORY_CSV)
    rows = load_inventory(inventory_path)

    if not rows:
        raise SystemExit("El inventario no tiene cartas disponibles para mostrar.")

    cards_html_parts = [build_card_html(row) for row in rows]
    cards_html = "\n".join(cards_html_parts)

    html = build_html(cards_html)

    output_path = Path(OUTPUT_HTML)
    output_path.write_text(html, encoding="utf-8")

    print(f"[OK] Tienda generada en {output_path.resolve()}")


if __name__ == "__main__":
    main()
