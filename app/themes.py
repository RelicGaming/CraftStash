"""Per-user themes. Every theme is four colours (+ light/dark mode); the rest is derived so any combination stays coherent."""
import json, re

HEX = re.compile(r"#[0-9a-fA-F]{6}")
# key: (label, mode, background, cards, text, accent)
PRESETS = {
    "light":    ("Indigo light", "light", "#f4f5f7", "#ffffff", "#1d2030", "#3f46b8"),
    "dark":     ("Indigo dark",  "dark",  "#13141b", "#1b1d27", "#eceef6", "#9097ff"),
    "forest":   ("Forest",       "light", "#eff4ee", "#ffffff", "#1c2a20", "#2f7d4f"),
    "blush":    ("Blush",        "light", "#fbf2f4", "#ffffff", "#33202a", "#c2456f"),
    "ocean":    ("Ocean",        "light", "#edf4f7", "#ffffff", "#14262f", "#0f7a92"),
    "ember":    ("Ember",        "dark",  "#171210", "#221b17", "#f1e8e0", "#f08a4b"),
}
RADII = {"square": ("4px", "3px"), "soft": ("14px", "8px"), "round": ("22px", "14px")}
DEFAULT_CUSTOM = {"mode": "light", "bg": "#f4f5f7", "surface": "#ffffff", "ink": "#1d2030", "accent": "#3f46b8"}
STATUS = {"light": ("#b3261e", "#1e7a4f", "#8f5200"), "dark": ("#ff8a80", "#6fd3a3", "#f0b868")}
DARK_INK = "#111322"

def _rgb(h): h = h.lstrip("#"); return [int(h[i:i + 2], 16) for i in (0, 2, 4)]
def _hex(c): return "#%02x%02x%02x" % tuple(int(round(max(0, min(255, v)))) for v in c)
def mix(a, b, p): return _hex([x * p + y * (1 - p) for x, y in zip(_rgb(a), _rgb(b))])   # p = share of a

def lum(h):
    r, g, b = [(c / 255 / 12.92) if c / 255 <= .03928 else ((c / 255 + .055) / 1.055) ** 2.4 for c in _rgb(h)]
    return .2126 * r + .7152 * g + .0722 * b

def contrast(a, b):
    x, y = lum(a), lum(b); return (max(x, y) + .05) / (min(x, y) + .05)

def on(h): return "#ffffff" if contrast(h, "#ffffff") >= contrast(h, DARK_INK) else DARK_INK

def build_vars(mode, bg, surface, ink, accent):
    d, o, w = STATUS[mode]; r, g, b = _rgb(accent); soft = .16 if mode == "dark" else .12
    v = {"bg": bg, "surface": surface, "ink": ink, "sunk": mix(ink, surface, .06), "line": mix(ink, surface, .12),
         "stitch": mix(ink, surface, .30), "muted": mix(ink, surface, .62), "accent": accent, "accent-ink": on(accent),
         "accent-soft": mix(accent, surface, .14), "danger": d, "danger-soft": mix(d, surface, soft),
         "ok": o, "ok-soft": mix(o, surface, soft), "warn": w, "warn-soft": mix(w, surface, soft),
         "focus": f"0 0 0 3px rgba({r},{g},{b},.32)",
         "av-bg": "35% 26%" if mode == "dark" else "55% 88%", "av-fg": "75% 84%" if mode == "dark" else "45% 28%"}
    return v

def _hexes(v, fallback):
    return v if isinstance(v, str) and HEX.fullmatch(v) else fallback

def normalize(data):
    """Whatever was stored or posted -> a safe dict: name, radius, custom{mode,bg,surface,ink,accent}."""
    data = data if isinstance(data, dict) else {}
    name = data.get("name")
    name = name if name in PRESETS or name in ("system", "custom") else "system"
    radius = data.get("radius") if data.get("radius") in RADII else "soft"
    c = data.get("custom") if isinstance(data.get("custom"), dict) else {}
    custom = {k: _hexes(c.get(k), DEFAULT_CUSTOM[k]) for k in ("bg", "surface", "ink", "accent")}
    custom["mode"] = c.get("mode") if c.get("mode") in STATUS else "light"
    return {"name": name, "radius": radius, "custom": custom}

def load(stored):
    try: return normalize(json.loads(stored or "{}"))
    except (ValueError, TypeError): return normalize({})

def from_form(form):
    return normalize({"name": form.get("theme"), "radius": form.get("radius"),
                      "custom": {k: form.get(k) for k in ("mode", "bg", "surface", "ink", "accent")}})

def css_for(stored):
    t = load(stored); v = {}
    if t["name"] in PRESETS: _, mode, *cols = PRESETS[t["name"]]; v = build_vars(mode, *cols)
    elif t["name"] == "custom":
        c = t["custom"]; mode = c["mode"]; v = build_vars(mode, c["bg"], c["surface"], c["ink"], c["accent"])
    if t["radius"] != "soft": v["r-lg"], v["r-md"] = RADII[t["radius"]]
    if not v: return ""
    body = ";".join(f"--{k}:{x}" for k, x in v.items())
    if t["name"] in PRESETS or t["name"] == "custom": body += f";color-scheme:{mode}"
    return ":root{" + body + "}"

def preset_list():
    return [{"key": k, "label": l, "mode": m, "bg": bg, "surface": s, "ink": i, "accent": a, "accent_ink": on(a)}
            for k, (l, m, bg, s, i, a) in PRESETS.items()]
