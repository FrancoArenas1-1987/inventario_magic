import csv
import json
from pathlib import Path
from typing import List, Dict

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
        n = int(float(value))
    except ValueError:
        return value
    return f"{n:,}".replace(",", ".")


def load_inventory(path: Path) -> List[Dict]:
    """
    Carga el inventario desde CSV y devuelve una lista de filas (dicts).
    Solo considera cartas con status = 'available' y quantity > 0.
    """
    if not path.exists():
        raise SystemExit(f"No se encontró el archivo de inventario: {path}")

    rows: List[Dict] = []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            status = (row.get("status") or "").strip().lower()
            quantity_str = (row.get("quantity") or "0").strip() or "0"
            try:
                quantity = int(float(quantity_str))
            except ValueError:
                quantity = 0

            if status != "available" or quantity <= 0:
                continue

            # Normalizar algunos campos
            row["quantity"] = quantity
            row["price_display"] = format_clp((row.get("price_clp") or "").strip())
            row["is_foil"] = (row.get("is_foil") or "").strip().lower()
            row["image_url"] = (row.get("image_url") or "").strip()
            rows.append(row)
    return rows


def prepare_cards_for_frontend(rows: List[Dict]) -> List[Dict]:
    """
    Transforma las filas del CSV en una lista de dicts con solo
    los campos necesarios para el front.
    """
    cards = []
    for row in rows:
        name = (row.get("name") or "").strip()
        mtg_set = (row.get("set") or "").strip()
        lang = (row.get("lang") or "").strip()
        condition = (row.get("condition") or "").strip()
        is_foil_raw = (row.get("is_foil") or "").strip().lower()
        fmt = (row.get("format") or "").strip()
        quantity = row.get("quantity", 0)
        price_display = row.get("price_display") or "Consultar"
        image_file = row.get("image_url") or ""
        price_usd_ref = (row.get("price_usd_ref") or "").strip()

        is_foil = is_foil_raw in {"yes", "y", "foil", "true", "1", "si", "sí"}

        cards.append(
            {
                "name": name,
                "set": mtg_set,
                "lang": lang,
                "condition": condition,
                "isFoil": is_foil,
                "format": fmt,
                "quantity": quantity,
                "price": price_display,
                "priceUsdRef": price_usd_ref,
                "imageFile": image_file,
            }
        )
    return cards


def build_html(cards: List[Dict]) -> str:
    """
    Construye el HTML completo de la tienda, con paginación en el front.
    """
    cards_json = json.dumps(cards, ensure_ascii=False)
    image_base_path = IMAGE_RELATIVE_PATH.replace("\\", "/")

    template = """<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8" />
    <title>Tienda de Cartas Magic</title>
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <style>
        :root {{
            --bg-color: #050816;
            --bg-card: #0b1020;
            --bg-card-hover: #151b33;
            --accent: #08d9d6;
            --accent-soft: rgba(8, 217, 214, 0.2);
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --danger: #f97373;
            --border-radius-lg: 18px;
            --shadow-soft: 0 20px 40px rgba(15, 23, 42, 0.6);
        }}

        * {{
            box-sizing: border-box;
        }}

        body {{
            margin: 0;
            padding: 0;
            font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            background: radial-gradient(circle at top, #181824 0, #050816 40%, #020617 100%);
            color: var(--text-main);
        }}

        .page {{
            min-height: 100vh;
            display: flex;
            flex-direction: column;
        }}

        header {{
            padding: 1.5rem 1.5rem 0.75rem;
        }}

        .header-inner {{
            max-width: 1200px;
            margin: 0 auto;
        }}

        h1 {{
            margin: 0 0 0.2rem;
            font-size: clamp(1.8rem, 3vw, 2.3rem);
            letter-spacing: 0.08em;
            text-transform: uppercase;
        }}

        .subtitle {{
            margin: 0;
            color: var(--text-muted);
            font-size: 0.95rem;
        }}

        .badge-row {{
            margin-top: 0.6rem;
            display: flex;
            flex-wrap: wrap;
            gap: 0.6rem;
            font-size: 0.8rem;
        }}

        .badge {{
            padding: 0.25rem 0.6rem;
            border-radius: 999px;
            background: rgba(15, 23, 42, 0.9);
            border: 1px solid rgba(148, 163, 184, 0.4);
        }}

        .badge-accent {{
            border-color: rgba(8, 217, 214, 0.6);
            background: var(--accent-soft);
        }}

        .content {{
            flex: 1;
            padding: 0.5rem 1.5rem 2rem;
        }}

        .content-inner {{
            max-width: 1200px;
            margin: 0 auto;
        }}

        .toolbar {{
            display: flex;
            flex-wrap: wrap;
            gap: 0.75rem;
            align-items: center;
            margin-bottom: 1rem;
        }}

        .search-box {{
            position: relative;
            flex: 1 1 260px;
        }}

        .search-box input {{
            width: 100%;
            padding: 0.6rem 0.8rem 0.6rem 2.2rem;
            border-radius: 999px;
            border: 1px solid rgba(148, 163, 184, 0.6);
            background: rgba(15, 23, 42, 0.9);
            color: var(--text-main);
            outline: none;
        }}

        .search-box input::placeholder {{
            color: var(--text-muted);
        }}

        .search-icon {{
            position: absolute;
            left: 0.8rem;
            top: 50%;
            transform: translateY(-50%);
            font-size: 0.9rem;
            color: var(--text-muted);
        }}

        .toolbar-right {{
            display: flex;
            align-items: center;
            gap: 0.75rem;
            flex-wrap: wrap;
            font-size: 0.8rem;
            color: var(--text-muted);
        }}

        .toolbar-pill {{
            padding: 0.35rem 0.8rem;
            border-radius: 999px;
            border: 1px solid rgba(148, 163, 184, 0.5);
            background: rgba(15, 23, 42, 0.9);
        }}

        .counter-strong {{
            color: var(--accent);
            font-weight: 600;
        }}

        .cards-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
            gap: 1rem;
        }}

        .card {{
            background: radial-gradient(circle at top left, rgba(8, 217, 214, 0.08), transparent 55%),
                        radial-gradient(circle at bottom right, rgba(59, 130, 246, 0.1), transparent 55%),
                        var(--bg-card);
            border-radius: var(--border-radius-lg);
            box-shadow: var(--shadow-soft);
            padding: 0.65rem 0.65rem 0.75rem;
            display: flex;
            flex-direction: column;
            border: 1px solid rgba(15, 23, 42, 0.8);
            position: relative;
            overflow: hidden;
        }}

        .card::before {{
            content: "";
            position: absolute;
            inset: 0;
            border-radius: inherit;
            border: 1px solid rgba(148, 163, 184, 0.35);
            opacity: 0;
            pointer-events: none;
            transition: opacity 0.2s ease-out;
        }}

        .card:hover::before {{
            opacity: 1;
        }}

        .card:hover {{
            background: var(--bg-card-hover);
            transform: translateY(-1px);
            transition: transform 0.12s ease-out, background 0.15s ease-out;
        }}

        .card-image-wrapper {{
            border-radius: 14px;
            overflow: hidden;
            aspect-ratio: 3 / 4;
            margin-bottom: 0.5rem;
            border: 1px solid rgba(15, 23, 42, 0.9);
            background: radial-gradient(circle at top, #020617 0, #020617 30%, #020617 100%);
            position: relative;
        }}

        .card-image-wrapper img {{
            width: 100%;
            height: 100%;
            object-fit: cover;
            display: block;
        }}

        .foil-chip {{
            position: absolute;
            top: 0.35rem;
            left: 0.35rem;
            padding: 0.18rem 0.5rem;
            border-radius: 999px;
            font-size: 0.65rem;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            background: linear-gradient(120deg, #22c55e, #a855f7, #0ea5e9);
            color: #0f172a;
            font-weight: 700;
            box-shadow: 0 0 12px rgba(34, 197, 94, 0.8);
        }}

        .card-body {{
            display: flex;
            flex-direction: column;
            gap: 0.22rem;
            font-size: 0.8rem;
        }}

        .card-name {{
            font-size: 0.92rem;
            font-weight: 600;
            line-height: 1.2;
        }}

        .card-tags {{
            display: flex;
            flex-wrap: wrap;
            gap: 0.35rem;
            color: var(--text-muted);
        }}

        .card-tag {{
            padding: 0.12rem 0.45rem;
            border-radius: 999px;
            border: 1px solid rgba(148, 163, 184, 0.5);
            font-size: 0.7rem;
        }}

        .card-footer {{
            display: flex;
            align-items: baseline;
            justify-content: space-between;
            margin-top: 0.3rem;
        }}

        .price-main {{
            font-size: 0.95rem;
            font-weight: 600;
        }}

        .price-ref {{
            font-size: 0.7rem;
            color: var(--text-muted);
        }}

        .qty-pill {{
            font-size: 0.7rem;
            padding: 0.15rem 0.5rem;
            border-radius: 999px;
            border: 1px solid rgba(148, 163, 184, 0.6);
        }}

        .pagination {{
            display: flex;
            justify-content: center;
            flex-wrap: wrap;
            gap: 0.25rem;
            margin-top: 1.2rem;
        }}

        .page-btn {{
            border-radius: 999px;
            padding: 0.25rem 0.55rem;
            border: 1px solid rgba(148, 163, 184, 0.45);
            background: rgba(15, 23, 42, 0.9);
            color: var(--text-main);
            font-size: 0.8rem;
            cursor: pointer;
            min-width: 32px;
        }}

        .page-btn[disabled] {{
            opacity: 0.35;
            cursor: default;
        }}

        .page-btn.active {{
            background: var(--accent-soft);
            border-color: var(--accent);
            color: var(--accent);
            font-weight: 600;
        }}

        .page-info {{
            text-align: center;
            margin-top: 0.5rem;
            color: var(--text-muted);
            font-size: 0.8rem;
        }}

        .empty-state {{
            margin-top: 2rem;
            text-align: center;
            color: var(--text-muted);
            font-size: 0.9rem;
        }}

        .empty-state strong {{
            color: var(--accent);
        }}

        footer {{
            padding: 0.75rem 1.5rem 1.2rem;
            border-top: 1px solid rgba(15, 23, 42, 0.9);
            background: radial-gradient(circle at bottom, #020617 0, #020617 45%, #000 100%);
        }}

        .footer-inner {{
            max-width: 1200px;
            margin: 0 auto;
            font-size: 0.78rem;
            color: var(--text-muted);
            display: flex;
            flex-wrap: wrap;
            gap: 0.25rem 0.75rem;
            align-items: center;
            justify-content: space-between;
        }}

        .footer-inner a {{
            color: var(--accent);
            text-decoration: none;
        }}

        @media (max-width: 640px) {{
            header {{
                padding: 1.2rem 1rem 0.5rem;
            }}
            .content {{
                padding: 0.25rem 1rem 1.5rem;
            }}
            footer {{
                padding: 0.6rem 1rem 1rem;
            }}
        }}
    </style>
</head>
<body>
<div class="page">
    <header>
        <div class="header-inner">
            <h1>El Castillo Magic</h1>
            <p class="subtitle">Catálogo de cartas &mdash; Modern, Commander y más.</p>
            <div class="badge-row">
                <span class="badge badge-accent">Inventario dinámico desde fotos</span>
                <span class="badge">Filtrado instantáneo</span>
                <span class="badge">Soporte para cartas foil ✨</span>
            </div>
        </div>
    </header>

    <main class="content">
        <div class="content-inner">
            <section class="toolbar">
                <div class="search-box">
                    <span class="search-icon">🔍</span>
                    <input
                        id="searchInput"
                        type="search"
                        placeholder="Buscar por nombre, edición, formato, idioma..."
                        autocomplete="off"
                    />
                </div>
                <div class="toolbar-right">
                    <div class="toolbar-pill">
                        Cartas visibles:
                        <span id="visibleCount" class="counter-strong">0</span>
                    </div>
                    <div class="toolbar-pill">
                        Total catálogo:
                        <span id="totalCount" class="counter-strong">0</span>
                    </div>
                </div>
            </section>

            <section id="cardsSection">
                <div id="cardsContainer" class="cards-grid"></div>
                <div id="emptyState" class="empty-state" style="display:none;">
                    No encontramos cartas para tu búsqueda.
                    <br />
                    Prueba con otro nombre, edición o <strong>limpia el filtro</strong>.
                </div>
                <div id="pagination" class="pagination"></div>
                <div id="pageInfo" class="page-info"></div>
            </section>
        </div>
    </main>

    <footer>
        <div class="footer-inner">
            <span>Generado automáticamente desde inventario_cartas.csv.</span>
            <span>El Castillo Magic &copy; 2025</span>
        </div>
    </footer>
</div>

<script>
    const IMAGE_BASE_PATH = "{image_base_path}";
    const PAGE_SIZE = 60;
    const cardsData = {cards_json};

    let filteredCards = [...cardsData];
    let currentPage = 1;

    const $ = (id) => document.getElementById(id);

    function debounce(fn, delay) {{
        let timer = null;
        return (...args) => {{
            if (timer) clearTimeout(timer);
            timer = setTimeout(() => fn(...args), delay);
        }};
    }}

    function buildCardElement(card) {{
        const article = document.createElement("article");
        article.className = "card";

        const imageWrapper = document.createElement("div");
        imageWrapper.className = "card-image-wrapper";

        const img = document.createElement("img");
        img.loading = "lazy";
        img.alt = card.name || "Carta Magic";
        img.src = IMAGE_BASE_PATH + "/" + encodeURI(card.imageFile);
        imageWrapper.appendChild(img);

        if (card.isFoil) {{
            const foilChip = document.createElement("div");
            foilChip.className = "foil-chip";
            foilChip.textContent = "FOIL";
            imageWrapper.appendChild(foilChip);
        }}

        const body = document.createElement("div");
        body.className = "card-body";

        const nameEl = document.createElement("div");
        nameEl.className = "card-name";
        nameEl.textContent = card.name;
        body.appendChild(nameEl);

        const tags = document.createElement("div");
        tags.className = "card-tags";

        const tagSet = document.createElement("span");
        tagSet.className = "card-tag";
        tagSet.textContent = (card.set || "").toUpperCase() || "SET";
        tags.appendChild(tagSet);

        if (card.format) {{
            const tagFormat = document.createElement("span");
            tagFormat.className = "card-tag";
            tagFormat.textContent = card.format;
            tags.appendChild(tagFormat);
        }}

        if (card.lang) {{
            const tagLang = document.createElement("span");
            tagLang.className = "card-tag";
            tagLang.textContent = card.lang.toUpperCase();
            tags.appendChild(tagLang);
        }}

        if (card.condition) {{
            const tagCond = document.createElement("span");
            tagCond.className = "card-tag";
            tagCond.textContent = card.condition;
            tags.appendChild(tagCond);
        }}

        body.appendChild(tags);

        const footer = document.createElement("div");
        footer.className = "card-footer";

        const priceBox = document.createElement("div");
        const priceMain = document.createElement("div");
        priceMain.className = "price-main";
        priceMain.textContent = "$ " + card.price;
        priceBox.appendChild(priceMain);

        if (card.priceUsdRef) {{
            const priceRef = document.createElement("div");
            priceRef.className = "price-ref";
            priceRef.textContent = "Ref: USD " + card.priceUsdRef;
            priceBox.appendChild(priceRef);
        }}

        footer.appendChild(priceBox);

        const qtyPill = document.createElement("div");
        qtyPill.className = "qty-pill";
        qtyPill.textContent = "Stock: " + (card.quantity || 1);
        footer.appendChild(qtyPill);

        body.appendChild(footer);

        article.appendChild(imageWrapper);
        article.appendChild(body);
        return article;
    }}

    function renderCards() {{
        const container = document.getElementById("cardsContainer");
        const emptyState = document.getElementById("emptyState");
        const totalCountEl = document.getElementById("totalCount");
        const visibleCountEl = document.getElementById("visibleCount");
        const pageInfo = document.getElementById("pageInfo");

        totalCountEl.textContent = String(cardsData.length);

        if (!filteredCards.length) {{
            container.innerHTML = "";
            emptyState.style.display = "block";
            visibleCountEl.textContent = "0";
            pageInfo.textContent = "";
            document.getElementById("pagination").innerHTML = "";
            return;
        }}

        emptyState.style.display = "none";

        const totalPages = Math.max(1, Math.ceil(filteredCards.length / PAGE_SIZE));
        if (currentPage > totalPages) {{
            currentPage = totalPages;
        }}

        const startIdx = (currentPage - 1) * PAGE_SIZE;
        const endIdx = startIdx + PAGE_SIZE;
        const pageCards = filteredCards.slice(startIdx, endIdx);

        container.innerHTML = "";
        for (const card of pageCards) {{
            container.appendChild(buildCardElement(card));
        }}

        visibleCountEl.textContent = String(pageCards.length);
        pageInfo.textContent = "Mostrando página " + currentPage + " de " + totalPages;

        renderPagination(totalPages);
    }}

    function renderPagination(totalPages) {{
        const pagination = document.getElementById("pagination");
        pagination.innerHTML = "";

        if (totalPages <= 1) {{
            return;
        }}

        const prevBtn = document.createElement("button");
        prevBtn.className = "page-btn";
        prevBtn.textContent = "←";
        prevBtn.disabled = currentPage === 1;
        prevBtn.onclick = () => {{
            if (currentPage > 1) {{
                currentPage--;
                renderCards();
            }}
        }};
        pagination.appendChild(prevBtn);

        const maxToShow = 7;
        let start = Math.max(1, currentPage - 3);
        let end = Math.min(totalPages, start + maxToShow - 1);
        if (end - start < maxToShow - 1) {{
            start = Math.max(1, end - maxToShow + 1);
        }}

        if (start > 1) {{
            const first = document.createElement("button");
            first.className = "page-btn";
            first.textContent = "1";
            first.onclick = () => {{
                currentPage = 1;
                renderCards();
            }};
            pagination.appendChild(first);

            if (start > 2) {{
                const dots = document.createElement("span");
                dots.className = "page-btn";
                dots.textContent = "…";
                pagination.appendChild(dots);
            }}
        }}

        for (let i = start; i <= end; i++) {{
            const btn = document.createElement("button");
            btn.className = "page-btn" + (i === currentPage ? " active" : "");
            btn.textContent = String(i);
            if (i !== currentPage) {{
                btn.onclick = () => {{
                    currentPage = i;
                    renderCards();
                }};
            }} else {{
                btn.disabled = true;
            }}
            pagination.appendChild(btn);
        }}

        if (end < totalPages) {{
            if (end < totalPages - 1) {{
                const dots = document.createElement("span");
                dots.className = "page-btn";
                dots.textContent = "…";
                pagination.appendChild(dots);
            }}

            const last = document.createElement("button");
            last.className = "page-btn";
            last.textContent = String(totalPages);
            last.onclick = () => {{
                currentPage = totalPages;
                renderCards();
            }};
            pagination.appendChild(last);
        }}

        const nextBtn = document.createElement("button");
        nextBtn.className = "page-btn";
        nextBtn.textContent = "→";
        nextBtn.disabled = currentPage === totalPages;
        nextBtn.onclick = () => {{
            if (currentPage < totalPages) {{
                currentPage++;
                renderCards();
            }}
        }};
        pagination.appendChild(nextBtn);
    }}

    function applyFilters() {{
        const input = document.getElementById("searchInput");
        const query = (input.value || "").trim().toLowerCase();

        if (!query) {{
            filteredCards = [...cardsData];
        }} else {{
            filteredCards = cardsData.filter((card) => {{
                const haystack = [
                    card.name || "",
                    card.set || "",
                    card.format || "",
                    card.lang || "",
                    card.condition || ""
                ]
                    .join(" ")
                    .toLowerCase();

                return haystack.includes(query);
            }});
        }}

        currentPage = 1;
        renderCards();
    }}

    function init() {{
        const input = document.getElementById("searchInput");
        input.addEventListener("input", debounce(applyFilters, 200));

        filteredCards = [...cardsData];
        currentPage = 1;
        renderCards();
    }}

    document.addEventListener("DOMContentLoaded", init);
</script>
</body>
</html>
"""
    return template.format(
        image_base_path=image_base_path,
        cards_json=cards_json,
    )


def main() -> None:
    inventory_path = Path(INVENTORY_CSV)
    rows = load_inventory(inventory_path)

    if not rows:
        raise SystemExit("El inventario no tiene cartas disponibles para mostrar.")

    cards = prepare_cards_for_frontend(rows)
    html = build_html(cards)

    output_path = Path(OUTPUT_HTML)
    output_path.write_text(html, encoding="utf-8")

    print(f"[OK] Tienda generada en {output_path.resolve()}")


if __name__ == "__main__":
    main()
