"""CSV import. Works inside the caller's transaction so a dry run can simply roll back."""
import csv, io, json, math, re, secrets

HEX = re.compile(r"#[0-9a-fA-F]{6}")
CANON = {"material type": "type", "type": "type", "material": "type", "name": "name", "item": "name",
         "colour": "colour", "color": "colour", "variant": "subtype", "in stock": "qty", "stock": "qty",
         "qty": "qty", "quantity": "qty", "unit": "unit", "price per unit": "price", "price": "price",
         "low stock at": "low", "low stock": "low", "location": "location", "notes": "notes",
         "swatch (hex)": "hex", "swatch": "hex", "hex": "hex"}
MAX_ROWS = 5000
TEMPLATE = ("Material type,Name,Colour,Variant,In stock,Unit,Price per unit,Low stock at,Location,Notes,Swatch (hex),Width,Fibre\r\n"
            "Fabric,Poplin,White,Cotton poplin,3,m,12.50,1,Shelf 2,,#ffffff,150,Cotton\r\n"
            "Thread,Sew-all,Red,Polyester,5,spool,3.20,2,Drawer 1,Good for sewing,#cc2222,,\r\n")

def to_num(v):
    if not (v or "").strip(): return None
    s = re.sub(r"[^\d.,-]", "", v)
    if not s: raise ValueError(v)                     # text like "lots" is an error, not an empty cell
    s = s.replace(",", ".") if "," in s and "." not in s else s.replace(",", "")
    x = float(s)                                  # ValueError on junk like 1.2.3
    if not math.isfinite(x) or x < 0: raise ValueError(v)
    return x

def run_import(d, pool_id, text, update=True, default_type="Imported"):
    text = text.lstrip("\ufeff")
    delim = max(",;\t", key=text.split("\n", 1)[0].count)          # Excel in some locales saves with ; or tabs
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    if not rows or not any(c.strip() for c in rows[0]): raise ValueError("The file is empty.")
    header = [h.strip() for h in rows[0]]
    canon = [CANON.get(re.sub(r"\s+", " ", h.lower())) for h in header]
    if "name" not in canon:
        raise ValueError("The first row must be a header with at least a Name column. Download the template to see the format.")
    extras = [(i, h[:40]) for i, (h, c) in enumerate(zip(header, canon)) if c is None and h]
    types = {t["name"].lower(): {"id": t["id"], "fl": json.loads(t["fields"]), "dirty": False}
             for t in d.execute("SELECT id, name, fields FROM types WHERE pool_id=?", (pool_id,)).fetchall()}
    res = {"added": 0, "updated": 0, "skipped": 0, "new_types": 0, "new_fields": 0, "errors": []}

    def bad(n, msg):
        res["skipped"] += 1
        if len(res["errors"]) < 8: res["errors"].append(f"Row {n}: {msg}")

    for n, row in enumerate(rows[1:], 2):
        if not any(c.strip() for c in row): continue
        if n - 1 > MAX_ROWS: res["errors"].append(f"Stopped after {MAX_ROWS} rows."); break
        r = {c: row[i].strip() for i, c in enumerate(canon) if c and i < len(row)}
        name = r.get("name", "")[:120]
        if not name: bad(n, "no name"); continue
        try: qty, price, low = to_num(r.get("qty")), to_num(r.get("price")), to_num(r.get("low"))
        except ValueError as e: bad(n, f"'{e}' is not a valid number"); continue
        tname = (r.get("type") or default_type)[:60]
        t = types.get(tname.lower())
        if not t:
            unit = (r.get("unit") or "pcs")[:20]
            cur = d.execute("INSERT INTO types(pool_id,name,unit,fields) VALUES(?,?,?,'[]')", (pool_id, tname, unit))
            t = types[tname.lower()] = {"id": cur.lastrowid, "fl": [], "dirty": False}; res["new_types"] += 1
        ex = {}
        for i, label in extras:
            val = row[i].strip()[:200] if i < len(row) else ""
            if not val: continue
            f = next((f for f in t["fl"] if f["label"].lower() == label.lower()), None)
            if not f:
                if len(t["fl"]) >= 20: continue
                f = {"id": "f" + secrets.token_hex(3), "label": label, "type": "text", "options": []}
                t["fl"].append(f); t["dirty"] = True; res["new_fields"] += 1
            ex[f["id"]] = val
        colour = r.get("colour", "")[:60]
        vals = {}
        if "subtype" in r: vals["subtype"] = r["subtype"][:60]
        if r.get("qty", "") != "": vals["qty"] = qty
        if "price" in r: vals["price"] = price
        if "low" in r: vals["low_at"] = low
        if "location" in r: vals["location"] = r["location"][:80]
        if "notes" in r: vals["notes"] = r["notes"][:1000]
        if HEX.fullmatch(r.get("hex", "")): vals["hex"] = r["hex"]
        old = d.execute("SELECT id, extra FROM items WHERE type_id=? AND name=? COLLATE NOCASE AND colour=? COLLATE NOCASE",
                        (t["id"], name, colour)).fetchone() if update else None
        if old:
            extra = json.loads(old["extra"]); extra.update(ex); vals["extra"] = json.dumps(extra)
            d.execute("UPDATE items SET " + ",".join(f"{k}=?" for k in vals) + " WHERE id=?", [*vals.values(), old["id"]])
            res["updated"] += 1
        else:
            vals.update(type_id=t["id"], name=name, colour=colour, extra=json.dumps(ex))
            d.execute("INSERT INTO items(" + ",".join(vals) + ") VALUES(" + ",".join("?" * len(vals)) + ")", list(vals.values()))
            res["added"] += 1
    for t in types.values():
        if t["dirty"]: d.execute("UPDATE types SET fields=? WHERE id=?", (json.dumps(t["fl"]), t["id"]))
    return res
