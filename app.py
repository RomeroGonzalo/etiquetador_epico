import re
from typing import Optional

import fitz
import pandas as pd
import streamlit as st

st.set_page_config(page_title="Enriquecedor de Etiquetas", page_icon="🏷️", layout="centered")
st.title("Enriquecedor de Etiquetas")
st.caption("Subí el PDF y el Excel. Descargás el PDF con el nuevo diseño listo para imprimir.")

col1, col2 = st.columns(2)
with col1:
    pdf_file = st.file_uploader("PDF de etiquetas", type="pdf")
with col2:
    excel_file = st.file_uploader("Excel — hoja 'Carga'", type=["xlsx", "xls"])


# ── helpers ───────────────────────────────────────────────────────────────────

def normalize(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(s).strip().upper())


def fmt_precio(s: str) -> str:
    """'15000' → '15.000'  (punto como separador de miles, sin decimales)"""
    try:
        n = int(float(re.sub(r"[.,\s$]", "", str(s).strip())))
        return f"{n:,}".replace(",", ".")
    except (ValueError, TypeError):
        return s


def insert_left_mixed(
    page: fitz.Page,
    bold_part: str,
    regular_part: str,
    y: float,
    fontsize: float,
    left_x: float,
    max_w: float,
) -> None:
    """Renders bold_part (hebo) + regular_part (helv) on the same baseline, left-aligned.
    Shrinks font size until both parts fit together."""
    min_fs = max(fontsize * 0.55, 4.0)
    fs = fontsize
    while fs >= min_fs:
        tw_b = fitz.get_text_length(bold_part,    fontname="hebo", fontsize=fs)
        tw_r = fitz.get_text_length(regular_part, fontname="helv", fontsize=fs)
        if tw_b + tw_r <= max_w:
            break
        fs -= 0.25
    tw_b = fitz.get_text_length(bold_part, fontname="hebo", fontsize=fs)
    page.insert_text((left_x,          y), bold_part,    fontsize=fs, fontname="hebo", color=(0, 0, 0))
    page.insert_text((left_x + tw_b,   y), regular_part, fontsize=fs, fontname="helv", color=(0, 0, 0))


def insert_left(
    page: fitz.Page,
    text: str,
    y: float,
    fontsize: float,
    fontname: str,
    left_x: float,
    max_w: float,
) -> None:
    """
    Left-aligned text insertion with auto-fit:
    1. Shrinks font size down to 55 % of original to fit on one line.
    2. If still overflows, wraps to 2 lines at minimum size.
    """
    min_fs = max(fontsize * 0.55, 4.0)
    fs = fontsize

    while fs >= min_fs:
        if fitz.get_text_length(text, fontname=fontname, fontsize=fs) <= max_w:
            page.insert_text((left_x, y), text, fontsize=fs, fontname=fontname, color=(0, 0, 0))
            return
        fs -= 0.25

    # Wrap to at most 2 lines at min font size
    fs = min_fs
    words = text.split()
    line1: list[str] = []
    line2: list[str] = []
    for word in words:
        if not line2:
            candidate = " ".join(line1 + [word])
            if fitz.get_text_length(candidate, fontname=fontname, fontsize=fs) <= max_w:
                line1.append(word)
            else:
                line2.append(word)
        else:
            line2.append(word)

    page.insert_text((left_x, y), " ".join(line1), fontsize=fs, fontname=fontname, color=(0, 0, 0))
    if line2:
        page.insert_text(
            (left_x, y + fs * 1.3), " ".join(line2),
            fontsize=fs, fontname=fontname, color=(0, 0, 0),
        )


def find_barcode_graphic_rect(page: fitz.Page) -> Optional[fitz.Rect]:
    """Detect barcode bounds from filled-black vector paths."""
    bars = []
    for d in page.get_drawings():
        fill = d.get("fill")
        r    = d.get("rect")
        if r is None or fill is None:
            continue
        if isinstance(fill, (int, float)):
            black = fill < 0.15
        elif isinstance(fill, tuple):
            black = (fill[0] < 0.15) if len(fill) == 1 else all(c < 0.15 for c in fill[:3])
        else:
            black = False
        if black and r.height > 1:
            bars.append(r)

    if len(bars) < 5:
        return None
    return fitz.Rect(
        min(b.x0 for b in bars), min(b.y0 for b in bars),
        max(b.x1 for b in bars), max(b.y1 for b in bars),
    )


def parse_label(page: fitz.Page) -> dict:
    """Extract product name, SKU, barcode code and barcode graphic bounds."""
    h, w = page.rect.height, page.rect.width

    blocks = sorted(
        [{"y0": b[1], "y1": b[3], "yc": (b[1]+b[3])/2, "text": b[4].strip()}
         for b in page.get_text("blocks") if b[6] == 0 and b[4].strip()],
        key=lambda b: b["yc"],
    )

    if not blocks:
        return {"name": "?", "sku": "?", "code": "?",
                "barcode_rect": fitz.Rect(0, h*.3, w, h*.7)}

    bc_block = next((b for b in reversed(blocks) if b["yc"] < h * 0.85), blocks[-1])
    bc_code  = bc_block["text"]

    first = blocks[0]
    lines = [ln.strip() for ln in first["text"].split("\n") if ln.strip()]
    if len(lines) >= 2:
        sku  = lines[0]
        name = " ".join(lines[1:])
    else:
        name = lines[0] if lines else ""
        sku  = re.sub(r"!+$", "", bc_code).strip()

    bc_rect = find_barcode_graphic_rect(page) or fitz.Rect(0, first["y1"]+1, w, bc_block["y0"]-1)
    return {"name": name, "sku": sku, "code": bc_code, "barcode_rect": bc_rect}


def build_page(
    src_doc: fitz.Document,
    page_idx: int,
    info: dict,
    rubro: str,
    precio: str,
    out_doc: fitz.Document,
) -> fitz.Page:
    """
    New label layout (left-aligned text, barcode centered at bottom):

        PRODUCT NAME            ← bold, left, auto-shrink/wrap
        SKU: CODE               ← bold, left
        RUBRO: CATEGORY         ← bold, left, uppercase
        $ PRICE                 ← bold, left, same size as name
        [ ||||||||||||||||| ]   ← barcode centrado, zona inferior
    """
    w = src_doc[page_idx].rect.width
    h = src_doc[page_idx].rect.height

    new = out_doc.new_page(width=w, height=h)
    new.draw_rect(fitz.Rect(0, 0, w, h), color=None, fill=(1, 1, 1))

    # ── layout zones — must sum to 1.0 ──────────────────────────────────────
    # top_margin | name | sku | rubro | precio | barcode | bot_margin
    #    0.04      0.15   0.11   0.11    0.15     0.40      0.04
    marg   = h * 0.04
    h_name = h * 0.15
    h_sku  = h * 0.11
    h_rub  = h * 0.11
    h_pre  = h * 0.20   # larger zone → bigger price font
    h_bc   = h * 0.35   # give the extra 5 % to precio
    # 0.04 + 0.15 + 0.11 + 0.11 + 0.20 + 0.35 + 0.04 = 1.00 ✓

    def fs(zone: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, zone * 0.75))

    left_x = w * 0.04
    max_w  = w - 2 * left_x

    # Baseline positions (82 % through each zone)
    y = marg
    def nb(zh: float) -> float:
        nonlocal y
        b = y + zh * 0.82
        y += zh
        return b

    y_name = nb(h_name)
    y_sku  = nb(h_sku)
    y_rub  = nb(h_rub)
    y_pre  = nb(h_pre)
    bc_top = y;  y += h_bc

    # Font sizes
    fs_name = fs(h_name, 6.0, 36.0)
    fs_sku  = fs(h_sku,  5.0, 28.0)
    fs_rub  = fs(h_rub,  5.0, 28.0)
    fs_pre  = fs(h_pre,  5.5, 36.0)   # same zone as name → same size

    display_sku = re.sub(r"!+$", "", info["sku"]).strip()

    insert_left(new,       info["name"],                    y_name, fs_name, "hebo", left_x, max_w)
    insert_left_mixed(new, "SKU: ",   display_sku,          y_sku,  fs_sku,         left_x, max_w)
    insert_left_mixed(new, "RUBRO: ", rubro.upper(),        y_rub,  fs_rub,         left_x, max_w)
    insert_left(new,       f"$ {fmt_precio(precio)}",       y_pre,  fs_pre,  "hebo", left_x, max_w)

    # Barcode — centered, fills the bottom zone
    src_r = info["barcode_rect"]
    if src_r.height > 0 and src_r.width > 0:
        scale = min((w * 0.92) / src_r.width, h_bc / src_r.height)
        dw    = src_r.width  * scale
        dh    = src_r.height * scale
        dx    = (w - dw) / 2
        dy    = bc_top + (h_bc - dh) / 2
        new.show_pdf_page(
            fitz.Rect(dx, dy, dx + dw, dy + dh),
            src_doc, page_idx, clip=src_r,
        )

    return new


# ── main ─────────────────────────────────────────────────────────────────────

if pdf_file and excel_file:
    with st.spinner("Procesando…"):
        try:
            df = pd.read_excel(excel_file, sheet_name="Carga", dtype=str)
            df.columns = df.columns.str.strip()

            missing = {"SKU", "Rubro", "Precio"} - set(df.columns)
            if missing:
                st.error(f"Columnas faltantes en el Excel: {', '.join(sorted(missing))}")
                st.stop()

            df["_key"] = df["SKU"].apply(normalize)
            lookup: dict = df.set_index("_key").to_dict("index")

            src = fitz.open(stream=pdf_file.read(), filetype="pdf")
            out = fitz.open()
            rows = []

            for i in range(len(src)):
                info  = parse_label(src[i])
                key   = normalize(info["code"])
                match = lookup.get(key)

                rubro  = match["Rubro"].strip()  if match else "—"
                precio = match["Precio"].strip()  if match else "—"
                status = "✅" if match else "❌"

                rows.append({
                    "Pág.":       i + 1,
                    "Código PDF": info["code"],
                    "Nombre":     info["name"],
                    "SKU":        match["SKU"].strip() if match else "No encontrado",
                    "Rubro":      rubro,
                    "Precio":     f"$ {precio}" if match else "—",
                    "Estado":     status,
                })

                if match:
                    build_page(src, i, info, rubro, precio, out)
                else:
                    p = out.new_page(width=src[i].rect.width, height=src[i].rect.height)
                    p.show_pdf_page(p.rect, src, i)

            n_ok  = sum(1 for r in rows if r["Estado"] == "✅")
            n_err = len(rows) - n_ok

            if n_err:
                st.warning(f"{n_err} página(s) sin coincidencia — se copiaron sin cambios.")
            if n_ok:
                st.success(f"{n_ok} de {len(src)} etiqueta(s) procesadas.")

            st.dataframe(rows, use_container_width=True, hide_index=True)

            st.download_button(
                "Descargar PDF",
                data=out.tobytes(),
                file_name="etiquetas_final.pdf",
                mime="application/pdf",
                type="primary",
                use_container_width=True,
            )

        except Exception as exc:
            st.error(f"Error: {exc}")
            raise
