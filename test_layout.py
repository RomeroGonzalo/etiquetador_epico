"""Quick visual test — generates test_output_v3.pdf with the new layout."""
import re, fitz, glob, pandas as pd
from typing import Optional

def normalize(s):
    return re.sub(r"[^A-Z0-9]", "", str(s).strip().upper())

def fmt_precio(s):
    try:
        n = int(float(re.sub(r"[.,\s$]", "", str(s).strip())))
        return f"{n:,}".replace(",", ".")
    except (ValueError, TypeError):
        return s

def insert_left_mixed(page, bold_part, regular_part, y, fontsize, left_x, max_w):
    min_fs = max(fontsize * 0.55, 4.0)
    fs = fontsize
    while fs >= min_fs:
        tw_b = fitz.get_text_length(bold_part,    fontname="hebo", fontsize=fs)
        tw_r = fitz.get_text_length(regular_part, fontname="helv", fontsize=fs)
        if tw_b + tw_r <= max_w: break
        fs -= 0.25
    tw_b = fitz.get_text_length(bold_part, fontname="hebo", fontsize=fs)
    page.insert_text((left_x,        y), bold_part,    fontsize=fs, fontname="hebo", color=(0,0,0))
    page.insert_text((left_x + tw_b, y), regular_part, fontsize=fs, fontname="helv", color=(0,0,0))

def insert_left(page, text, y, fontsize, fontname, left_x, max_w):
    min_fs = max(fontsize * 0.55, 4.0)
    fs = fontsize
    while fs >= min_fs:
        if fitz.get_text_length(text, fontname=fontname, fontsize=fs) <= max_w:
            page.insert_text((left_x, y), text, fontsize=fs, fontname=fontname, color=(0,0,0))
            return
        fs -= 0.25
    fs = min_fs
    words = text.split()
    line1, line2 = [], []
    for word in words:
        if not line2:
            c = " ".join(line1 + [word])
            if fitz.get_text_length(c, fontname=fontname, fontsize=fs) <= max_w:
                line1.append(word)
            else:
                line2.append(word)
        else:
            line2.append(word)
    page.insert_text((left_x, y), " ".join(line1), fontsize=fs, fontname=fontname, color=(0,0,0))
    if line2:
        page.insert_text((left_x, y+fs*1.3), " ".join(line2), fontsize=fs, fontname=fontname, color=(0,0,0))

def find_barcode_graphic_rect(page):
    bars = []
    for d in page.get_drawings():
        fill = d.get("fill"); r = d.get("rect")
        if r is None or fill is None: continue
        if isinstance(fill, (int, float)): black = fill < 0.15
        elif isinstance(fill, tuple): black = (fill[0]<0.15) if len(fill)==1 else all(c<0.15 for c in fill[:3])
        else: black = False
        if black and r.height > 1: bars.append(r)
    if len(bars) < 5: return None
    return fitz.Rect(min(b.x0 for b in bars), min(b.y0 for b in bars),
                     max(b.x1 for b in bars), max(b.y1 for b in bars))

def parse_label(page):
    h, w = page.rect.height, page.rect.width
    blocks = sorted(
        [{"y0":b[1],"y1":b[3],"yc":(b[1]+b[3])/2,"text":b[4].strip()}
         for b in page.get_text("blocks") if b[6]==0 and b[4].strip()],
        key=lambda b: b["yc"]
    )
    if not blocks:
        return {"name":"?","sku":"?","code":"?","barcode_rect":fitz.Rect(0,h*.3,w,h*.7)}
    bc_block = next((b for b in reversed(blocks) if b["yc"] < h*0.85), blocks[-1])
    bc_code  = bc_block["text"]
    first    = blocks[0]
    lines    = [l.strip() for l in first["text"].split("\n") if l.strip()]
    if len(lines) >= 2: sku=lines[0]; name=" ".join(lines[1:])
    else: name=lines[0] if lines else ""; sku=re.sub(r"!+$","",bc_code).strip()
    bc_rect = find_barcode_graphic_rect(page) or fitz.Rect(0,first["y1"]+1,w,bc_block["y0"]-1)
    return {"name":name,"sku":sku,"code":bc_code,"barcode_rect":bc_rect}

def build_page(src_doc, page_idx, info, rubro, precio, out_doc):
    w = src_doc[page_idx].rect.width; h = src_doc[page_idx].rect.height
    new = out_doc.new_page(width=w, height=h)
    new.draw_rect(fitz.Rect(0,0,w,h), color=None, fill=(1,1,1))

    marg=h*.04; h_name=h*.15; h_sku=h*.11; h_rub=h*.11; h_pre=h*.20; h_bc=h*.35
    def fs(z,lo,hi): return max(lo,min(hi,z*0.75))
    left_x=w*0.04; max_w=w-2*left_x
    y=marg
    def nb(zh):
        nonlocal y; b=y+zh*.82; y+=zh; return b
    y_name=nb(h_name); y_sku=nb(h_sku); y_rub=nb(h_rub); y_pre=nb(h_pre); bc_top=y; y+=h_bc
    dsku = re.sub(r"!+$","",info["sku"]).strip()
    insert_left(      new, info["name"],              y_name, fs(h_name,6.0,36.0), "hebo", left_x, max_w)
    insert_left_mixed(new, "SKU: ",   dsku,            y_sku,  fs(h_sku, 5.0,28.0),        left_x, max_w)
    insert_left_mixed(new, "RUBRO: ", rubro.upper(),   y_rub,  fs(h_rub, 5.0,28.0),        left_x, max_w)
    insert_left(      new, f"$ {fmt_precio(precio)}",  y_pre,  fs(h_pre, 5.5,36.0), "hebo", left_x, max_w)

    src_r=info["barcode_rect"]
    if src_r.height>0 and src_r.width>0:
        sc=min((w*.92)/src_r.width, h_bc/src_r.height)
        dw=src_r.width*sc; dh=src_r.height*sc
        dx=(w-dw)/2; dy=bc_top+(h_bc-dh)/2
        new.show_pdf_page(fitz.Rect(dx,dy,dx+dw,dy+dh), src_doc, page_idx, clip=src_r)

df = pd.DataFrame({
    "SKU":    ["BUIDRU!!","BACEBE!!","AKI24058!!","AKI24052!!","CLCHICAL!!","AKI361!!","33507037"],
    "Rubro":  ["Buzos","Babuchas Bebe","Babuchas","Babuchas Nena","Colitas","Babuchas","Camisas"],
    "Precio": ["15000","8500","12000","11000","3500","9000","25000"],
})
df["_key"] = df["SKU"].apply(normalize)
lookup = df.set_index("_key").to_dict("index")

pdfs = glob.glob(r"C:\Users\ga_ro\Downloads\Etiqueta*.pdf")
pdfs.append(r"C:\Users\ga_ro\OneDrive\Escritorio\EtiquetaItem6967413566224111645.pdf")
# Exclude previously generated test files
pdfs = [p for p in pdfs if "enriquecidas" not in p and "redise" not in p]

out = fitz.open()
for pdf_path in sorted(set(pdfs)):
    src = fitz.open(pdf_path)
    fname = pdf_path.split("\\")[-1]
    for i in range(min(1, len(src))):
        info  = parse_label(src[i])
        key   = normalize(info["code"])
        match = lookup.get(key)
        if match:
            build_page(src, i, info, match["Rubro"], match["Precio"], out)
            print(f"OK  {fname}  name=[{info['name']}]  [{src[i].rect.width:.0f}x{src[i].rect.height:.0f}]")
        else:
            p=out.new_page(width=src[i].rect.width,height=src[i].rect.height)
            p.show_pdf_page(p.rect,src,i)
            print(f"--  {fname}  SKIP code=[{info['code']}]")

out.save(r"c:\Users\ga_ro\OneDrive\Escritorio\PROYECTOS\etiquetador\test_output_v6.pdf")
print(f"\n{len(out)} paginas -> test_output_v6.pdf")
