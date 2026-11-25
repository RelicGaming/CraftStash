"""The pixel pattern engine: a picture becomes a grid of stitches (or beads, or drills) in a limited set of colours, each
with a symbol, and the chart is drawn as a PNG or a printable PDF. Pure Pillow: no Flask and no database here.

Colours are compared in CIELAB, so a match looks right to the eye: CIEDE2000 to pick which thread stands in for a
colour, and the quicker CIE76 to sort the cells between the chosen few.
"""
import base64, io, json, math, zlib
from functools import lru_cache
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

MIN_SIDE, MAX_SIDE = 5, 250          # stitches across or down: 250 x 250 keeps a chart quick to make and to draw
SYMBOLS = list("XO+#=%@&$*/<>^~?!") + list("ABCDEFGHJKLMNPRSTUVWYZ") + list("abdeghkmnqrstuwy") + list("23456789")
MAX_COLOURS = 60
EMPTY = 255                          # a cell that is left unstitched (transparent in the picture)
CLOSE = 6.0                          # CIEDE2000 up to this counts as "close enough" to a colour you own

# ---------- catalogues ----------
CATALOGUE_DIR = Path(__file__).parent / "palettes"
CATALOGUES = {"dmc-floss": "DMC floss", "dmc-diamond": "DMC diamond drills", "perler-midi": "Perler beads (midi)",
              "hama-midi": "Hama beads (midi)", "artkal-s": "Artkal S beads"}

@lru_cache(maxsize=None)
def catalogue(cid):
    """{brand, product, unit, license, colors: [[code, name, hex]]} for a bundled catalogue (see palettes/NOTICE.md)."""
    if cid not in CATALOGUES: raise KeyError(cid)
    return json.loads((CATALOGUE_DIR / f"{cid}.json").read_text(encoding="utf-8"))

# ---------- crafts: what a cell is, and how much each colour needs ----------
CRAFTS = {
    "cross-stitch": {"label": "Cross-stitch", "unit": "skein", "cells": "stitches", "catalogue": "dmc-floss",
                     "fields": {"count": ("Fabric count (stitches per inch)", 14, 6, 40), "strands": ("Strands", 2, 1, 6),
                                "per_unit": ("Stitches per skein (0 = estimate)", 0, 0, 20000)}},
    "beads": {"label": "Fuse beads", "unit": "bead", "cells": "beads", "catalogue": "perler-midi",
              "fields": {"board": ("Pegboard size (pegs per side)", 29, 10, 100)}},
    "diamond": {"label": "Diamond painting", "unit": "drill", "cells": "drills", "catalogue": "dmc-diamond",
                "fields": {"spare": ("Spare drills (%)", 10, 0, 50), "drill_mm": ("Drill size (mm)", 2.5, 1, 5)}},
    "colourwork": {"label": "Knit or crochet colourwork", "unit": "m", "cells": "stitches", "catalogue": "",
                   "fields": {"sts10": ("Stitches per 10 cm", 22, 4, 60), "rows10": ("Rows per 10 cm", 30, 4, 80),
                              "cm_per_st": ("Yarn per stitch (cm)", 3, 0.5, 20)}},
}

def per_skein(s):
    """Full cross stitches one skein (8 m of 6 strands) covers: about 1800 on 14-count with 2 strands, more on finer fabric."""
    return s["per_unit"] or round(1800 * s["count"] / 14 * 2 / s["strands"])

def need(craft, s, n):
    """How much of a colour n cells need, in the craft's unit (skeins, beads, drills, metres)."""
    if craft == "cross-stitch": return n / per_skein(s)
    if craft == "diamond": return math.ceil(n * (1 + s["spare"] / 100))
    if craft == "colourwork": return round(n * s["cm_per_st"] / 100 * 1.1, 1)     # 10% for tails and joins
    return n

def size_lines(craft, s, w, h):
    """The finished size, and anything else worth knowing before you start."""
    if craft == "cross-stitch":
        return [f"{w} x {h} stitches on {s['count']:g}-count fabric: {w / s['count']:.1f} x {h / s['count']:.1f} in "
                f"({w / s['count'] * 2.54:.1f} x {h / s['count'] * 2.54:.1f} cm), before margins.",
                f"{s['strands']:g} strands. One skein covers about {per_skein(s):,} stitches."]
    if craft == "beads":
        b = int(s["board"]); boards = math.ceil(w / b) * math.ceil(h / b)
        return [f"{w} x {h} beads: about {w * 0.5:.1f} x {h * 0.5:.1f} cm with 5 mm beads.",
                f"{boards} pegboard{'s' if boards != 1 else ''} of {b} x {b}."]
    if craft == "diamond":
        return [f"{w} x {h} drills: a canvas of about {w * s['drill_mm'] / 10:.1f} x {h * s['drill_mm'] / 10:.1f} cm.",
                f"Includes {s['spare']:g}% spare drills of each colour."]
    return [f"{w} x {h} stitches: about {w / s['sts10'] * 10:.1f} x {h / s['rows10'] * 10:.1f} cm at your gauge.",
            f"Yarn is estimated at {s['cm_per_st']:g} cm a stitch plus 10%. Swatch first: it varies a lot with yarn and technique."]

UNITS = {"skein": {"skein", "sk"}, "bead": {"bead", "pc", "pcs", "piece"}, "drill": {"drill", "pc", "pcs", "piece"},
         "m": {"m", "metre", "meter"}}

def in_unit(amount, craft_unit, item_unit):
    """amount (in the craft's unit) in the item's unit, or None when they can't be compared (grams, balls, ...)."""
    u = (item_unit or "").strip().lower().rstrip("s.") or "pc"
    if u in UNITS.get(craft_unit, ()): return amount
    if craft_unit == "m" and u in ("yd", "yard"): return amount / 0.9144
    return None

# ---------- colour science ----------
def hex_rgb(h): h = h.lstrip("#"); return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
def rgb_hex(c): return "#%02X%02X%02X" % tuple(c)

def _lin(c):
    c /= 255; return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

@lru_cache(maxsize=65536)
def lab(rgb):
    r, g, b = (_lin(v) for v in rgb)
    x, y, z = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047, 0.2126 * r + 0.7152 * g + 0.0722 * b, (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883
    f = lambda t: t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116
    fx, fy, fz = f(x), f(y), f(z)
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))

def de76(a, b): return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2   # squared: only ever compared

def de2000(a, b):
    L1, a1, b1 = a; L2, a2, b2 = b
    C1, C2 = math.hypot(a1, b1), math.hypot(a2, b2); Cm = (C1 + C2) / 2
    G = 0.5 * (1 - math.sqrt(Cm ** 7 / (Cm ** 7 + 25 ** 7)))
    a1p, a2p = a1 * (1 + G), a2 * (1 + G)
    C1p, C2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360; h2p = math.degrees(math.atan2(b2, a2p)) % 360
    dL, dC = L2 - L1, C2p - C1p
    dh = 0 if C1p * C2p == 0 else (h2p - h1p if abs(h2p - h1p) <= 180 else h2p - h1p - 360 if h2p > h1p else h2p - h1p + 360)
    dH = 2 * math.sqrt(C1p * C2p) * math.sin(math.radians(dh / 2))
    Lm, Cmp = (L1 + L2) / 2, (C1p + C2p) / 2
    hm = h1p + h2p if C1p * C2p == 0 else ((h1p + h2p) / 2 if abs(h1p - h2p) <= 180 else (h1p + h2p + 360) / 2 if h1p + h2p < 360 else (h1p + h2p - 360) / 2)
    T = 1 - 0.17 * math.cos(math.radians(hm - 30)) + 0.24 * math.cos(math.radians(2 * hm)) + 0.32 * math.cos(math.radians(3 * hm + 6)) - 0.20 * math.cos(math.radians(4 * hm - 63))
    SL = 1 + 0.015 * (Lm - 50) ** 2 / math.sqrt(20 + (Lm - 50) ** 2); SC = 1 + 0.045 * Cmp; SH = 1 + 0.015 * Cmp * T
    RT = -2 * math.sqrt(Cmp ** 7 / (Cmp ** 7 + 25 ** 7)) * math.sin(math.radians(60 * math.exp(-((hm - 275) / 25) ** 2)))
    return math.sqrt((dL / SL) ** 2 + (dC / SC) ** 2 + (dH / SH) ** 2 + RT * (dC / SC) * (dH / SH))

def nearest(rgb, options, shortlist=6):
    """(index, CIEDE2000) of the option (each with "lab") closest to rgb: CIE76 picks a shortlist, CIEDE2000 decides."""
    l = lab(tuple(rgb))
    near = sorted(range(len(options)), key=lambda i: de76(l, options[i]["lab"]))[:shortlist]
    best = min(near, key=lambda i: de2000(l, options[i]["lab"]))
    return best, de2000(l, options[best]["lab"])

# ---------- picture -> chart ----------
def grid_size(im, width, height):
    w = max(MIN_SIDE, min(MAX_SIDE, int(width)))
    h = int(height) if height else round(w * im.height / im.width)
    return w, max(MIN_SIDE, min(MAX_SIDE, h))

def make(im, width, height=0, colours=20, candidates=None, dither=False, sharp=False, min_count=0):
    """The chart for a picture. candidates: [{"hex", ...}] to choose from (a catalogue or your stash), or None to pick
    the best colours freely. Returns {"w", "h", "palette": [{...the candidate's fields, "hex", "count", "symbol"}],
    "cells": [palette index or EMPTY, row by row]}."""
    colours = max(2, min(MAX_COLOURS, int(colours)))
    w, h = grid_size(im, width, height)
    small = im.convert("RGBA").resize((w, h), Image.NEAREST if sharp else Image.BOX)
    px = list(small.get_flattened_data())
    opaque = [p[:3] for p in px if p[3] >= 128]
    if not opaque: return {"w": w, "h": h, "palette": [], "cells": [EMPTY] * (w * h)}
    strip = Image.new("RGB", (len(opaque), 1)); strip.putdata(opaque)
    k = min(256, colours * 2 if candidates else colours)
    q = strip.quantize(colors=k, method=Image.Quantize.MEDIANCUT)
    pal = q.getpalette()[:3 * 256]
    clusters = [(n, tuple(pal[3 * i:3 * i + 3])) for n, i in q.getcolors(256)]
    if candidates:
        opts = [{**c, "lab": lab(hex_rgb(c["hex"]))} for c in candidates]
        votes = {}
        for n, rgb in clusters: i = nearest(rgb, opts)[0]; votes[i] = votes.get(i, 0) + n
        chosen = [opts[i] for i, _ in sorted(votes.items(), key=lambda kv: -kv[1])[:colours]]
    else:
        chosen = [{"hex": rgb_hex(rgb), "lab": lab(rgb)} for _, rgb in sorted(clusters, reverse=True)]
    assign = _assign(small, px, chosen, dither)
    while min_count and len(chosen) > 1:                  # fold colours used only a few times into their nearest neighbour
        counts = [0] * len(chosen)
        for a in assign:
            if a != EMPTY: counts[a] += 1
        rare = min((i for i in range(len(chosen)) if 0 < counts[i] < min_count), key=lambda i: counts[i], default=None)
        if rare is None: break
        keep = [i for i in range(len(chosen)) if i != rare]
        others = [chosen[i] for i in keep]; remap = {old: new for new, old in enumerate(keep)}
        assign = [EMPTY if a == EMPTY else remap[a] if a != rare else nearest_76(px[j][:3], others) for j, a in enumerate(assign)]
        chosen = others
    counts = [0] * len(chosen)
    for a in assign:
        if a != EMPTY: counts[a] += 1
    order = sorted((i for i in range(len(chosen)) if counts[i]), key=lambda i: -counts[i])
    remap = {old: new for new, old in enumerate(order)}
    palette = [{**{k: v for k, v in chosen[i].items() if k != "lab"}, "count": counts[i], "symbol": SYMBOLS[n]} for n, i in enumerate(order)]
    return {"w": w, "h": h, "palette": palette, "cells": [EMPTY if a == EMPTY else remap[a] for a in assign]}

def nearest_76(rgb, options):
    l = lab(tuple(rgb)); return min(range(len(options)), key=lambda i: de76(l, options[i]["lab"]))

def _assign(small, px, chosen, dither):
    """Each cell's index into chosen (EMPTY where the picture is transparent)."""
    if dither:
        flat = [v for c in chosen for v in hex_rgb(c["hex"])]
        palimg = Image.new("P", (1, 1)); palimg.putpalette(flat + flat[:3] * (256 - len(chosen)))
        idx = list(small.convert("RGB").quantize(palette=palimg, dither=Image.Dither.FLOYDSTEINBERG).get_flattened_data())
        return [EMPTY if p[3] < 128 else (i if i < len(chosen) else 0) for p, i in zip(px, idx)]
    cache = {}
    def pick(rgb):
        if rgb not in cache: cache[rgb] = nearest_76(rgb, chosen)
        return cache[rgb]
    return [EMPTY if p[3] < 128 else pick(p[:3]) for p in px]

# ---------- storing ----------
def pack(chart):
    return json.dumps({**chart, "cells": base64.b64encode(zlib.compress(bytes(chart["cells"]), 9)).decode()}, separators=(",", ":"))

def unpack(text):
    c = json.loads(text); c["cells"] = list(zlib.decompress(base64.b64decode(c["cells"]))); return c

# ---------- drawing ----------
def font(size): return ImageFont.load_default(size=size)

def ink(hexv):
    r, g, b = hex_rgb(hexv); return (0, 0, 0) if 0.299 * r + 0.587 * g + 0.114 * b > 140 else (255, 255, 255)

def preview(chart, scale=1):
    """Just the colours, one pixel (or scale pixels) a cell: the thumbnail, and the overview on the PDF's first page."""
    cols = [hex_rgb(p["hex"]) for p in chart["palette"]]
    im = Image.new("RGB", (chart["w"], chart["h"])); im.putdata([(255, 255, 255) if c == EMPTY else cols[c] for c in chart["cells"]])
    return im.resize((chart["w"] * scale, chart["h"] * scale), Image.NEAREST) if scale > 1 else im

def render(chart, style="both", cell=None, x0=0, y0=0, w=None, h=None):
    """The chart (or the part from column x0 / row y0, w x h cells) with symbols, grid lines every cell and bold ones
    every 10, numbers every 10 along the top and left, and arrows marking the centre. style: colour, symbols or both."""
    W, H = chart["w"], chart["h"]; w = w or W; h = h or H
    cell = cell or max(6, min(24, 2400 // max(w, h)))
    m = max(28, cell * 2)                                   # margin for the numbers
    img = Image.new("RGB", (w * cell + 2 * m, h * cell + 2 * m), "white"); d = ImageDraw.Draw(img)
    pal = chart["palette"]; cells = chart["cells"]
    if style != "symbols":
        part = Image.new("RGB", (w, h))
        part.putdata([(255, 255, 255) if (c := cells[(y0 + y) * W + x0 + x]) == EMPTY else hex_rgb(pal[c]["hex"]) for y in range(h) for x in range(w)])
        img.paste(part.resize((w * cell, h * cell), Image.NEAREST), (m, m))
    if style != "colour" and cell >= 9:
        f = font(int(cell * 0.75))
        for y in range(h):
            for x in range(w):
                c = cells[(y0 + y) * W + x0 + x]
                if c == EMPTY: continue
                p = pal[c]
                d.text((m + x * cell + cell / 2, m + y * cell + cell / 2), p["symbol"], font=f, anchor="mm",
                       fill=ink(p["hex"]) if style == "both" else (0, 0, 0))
    thin, bold = (205, 205, 205), (40, 40, 40)
    if cell >= 5:
        for x in range(w + 1):
            if (x0 + x) % 10: d.line([(m + x * cell, m), (m + x * cell, m + h * cell)], fill=thin)
        for y in range(h + 1):
            if (y0 + y) % 10: d.line([(m, m + y * cell), (m + w * cell, m + y * cell)], fill=thin)
    nf = font(max(10, min(16, m // 2)))
    for x in range(w + 1):
        if (x0 + x) % 10 == 0 or x in (0, w):
            d.line([(m + x * cell, m), (m + x * cell, m + h * cell)], fill=bold, width=2 if (x0 + x) % 10 == 0 else 1)
            if (x0 + x) % 10 == 0 and x0 + x: d.text((m + x * cell, m - 4), str(x0 + x), font=nf, anchor="md", fill=bold)
    for y in range(h + 1):
        if (y0 + y) % 10 == 0 or y in (0, h):
            d.line([(m, m + y * cell), (m + w * cell, m + y * cell)], fill=bold, width=2 if (y0 + y) % 10 == 0 else 1)
            if (y0 + y) % 10 == 0 and y0 + y: d.text((m - 4, m + y * cell), str(y0 + y), font=nf, anchor="rm", fill=bold)
    cx, cy = W / 2 - x0, H / 2 - y0                         # centre arrows, on the edges of the part that has the centre
    a = m // 3; red = (200, 30, 30)
    if 0 <= cx <= w:
        px = m + cx * cell
        d.polygon([(px - a, 2), (px + a, 2), (px, 2 + a)], fill=red)
        d.polygon([(px - a, img.height - 2), (px + a, img.height - 2), (px, img.height - 2 - a)], fill=red)
    if 0 <= cy <= h:
        py = m + cy * cell
        d.polygon([(2, py - a), (2, py + a), (2 + a, py)], fill=red)
        d.polygon([(img.width - 2, py - a), (img.width - 2, py + a), (img.width - 2 - a, py)], fill=red)
    return img

def png(img):
    b = io.BytesIO(); img.save(b, "PNG", optimize=True); return b.getvalue()

# ---------- the printable PDF ----------
PAGE = (1240, 1754)          # A4 at 150 dpi
TILE = (50, 70)              # cells on one chart page (multiples of 10, so the bold lines line up across pages)

def pdf(chart, title, lines, legend):
    """A4 pages: an overview with the size notes and the colour key, then the chart in parts with symbols.
    legend: [(symbol, hex, label, stitches, amount text)]."""
    pages = []; f, fb, fs = font(22), font(34), font(18)
    def page():
        p = Image.new("RGB", PAGE, "white"); pages.append(p); return p, ImageDraw.Draw(p)
    p, d = page(); y = 70
    d.text((70, y), title[:60], font=fb, fill="black"); y += 56
    for ln in lines: d.text((70, y), ln[:110], font=fs, fill=(60, 60, 60)); y += 28
    pv = preview(chart); scale = max(1, min(8, 1100 // pv.width, 620 // pv.height))
    pv = pv.resize((pv.width * scale, pv.height * scale), Image.NEAREST); p.paste(pv, (70, y + 16)); y += pv.height + 46
    d.text((70, y), "Colour key", font=f, fill="black"); y += 40
    for sym, hexv, label, n, amount in legend:
        if y > PAGE[1] - 90: p, d = page(); y = 70
        d.rectangle([70, y, 106, y + 30], fill=hex_rgb(hexv), outline=(90, 90, 90))
        d.text((88, y + 15), sym, font=fs, anchor="mm", fill=ink(hexv))
        d.text((124, y + 3), label[:58], font=fs, fill="black"); d.text((860, y + 3), f"{n:,}", font=fs, fill="black", anchor="ra")
        d.text((1170, y + 3), amount, font=fs, fill="black", anchor="ra"); y += 40
    parts = [(x, yy) for yy in range(0, chart["h"], TILE[1]) for x in range(0, chart["w"], TILE[0])]
    for n, (x, yy) in enumerate(parts, 1):
        p, d = page()
        w, h = min(TILE[0], chart["w"] - x), min(TILE[1], chart["h"] - yy)
        d.text((70, 40), f"{title[:50]} - part {n} of {len(parts)}: columns {x + 1}-{x + w}, rows {yy + 1}-{yy + h}", font=fs, fill="black")
        part = render(chart, "both", 20, x, yy, w, h)
        p.paste(part, ((PAGE[0] - part.width) // 2, 80))
    b = io.BytesIO()     # JPEG pages, no chroma subsampling: Pillow writes palette pages uncompressed (70 MB for a big chart)
    pages[0].save(b, "PDF", save_all=True, append_images=pages[1:], resolution=150, title=title[:60], quality=85, subsampling=0)
    return b.getvalue()
