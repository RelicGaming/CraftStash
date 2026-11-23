import io, re
from conftest import seed

def upload(c, text, **form):
    return c.post("/collections/%d/import" % form.pop("pid", 1), {"file": (io.BytesIO(text.encode()), "stash.csv"), **form}, follow_redirects=True).get_data(as_text=True)

def test_export_then_import_round_trips(env, amy):
    seed(amy); amy.post("/items/1/edit", {"name": "Merino", "colour": "Rose", "has_hex": "1", "hex": "#d98ca3", "qty": "5", "price": "9.5", "x_" + re.search(r'name="x_(\w+)"', amy.html("/items/1/edit")).group(1): "Wool&Co"})
    csv_text = amy.get("/collections/1/export.csv").get_data(as_text=True)
    amy.post("/collections", {"name": "Copy"})
    out = upload(amy, csv_text, pid=2, mode="update", dry="on")             # "check only" must not change anything
    assert "nothing was imported" in out and "2 added" in out and "Merino" not in amy.html("/collections/2")
    upload(amy, csv_text, pid=2, mode="update")                              # the real import
    page = amy.html("/collections/2"); assert "Merino" in page and "Cotton" in page and "d98ca3" in page and "Wool&amp;Co" in page and "Brand" in page

def test_import_updates_existing_and_reports_bad_rows(env, amy):
    seed(amy)
    text = "Material type,Name,Colour,In stock,Price per unit\nYarn,Merino,Rose,12,\u00a39.99\nYarn,Broken,Red,lots,1\nYarn,,Red,1,1\n"
    out = amy.post("/collections/1/import", {"file": (io.BytesIO(text.encode()), "s.csv"), "mode": "update"}, follow_redirects=True).get_data(as_text=True)
    assert "1 updated" in out and "2 skipped" in out and "lots" in out and "is not a valid number" in out and "no name" in out
    page = amy.html("/collections/1"); assert "<strong>12</strong>" in page and "9.99" in page and "Broken" not in page

def test_import_creates_types_and_fields_and_accepts_semicolons(env, amy):
    amy.post("/collections", {"name": "Sewing"})
    text = "Material type;Name;Unit;In stock;Width\nFabric;Poplin;m;3,5;150\n"
    amy.post("/collections/1/import", {"file": (io.BytesIO(text.encode("cp1252")), "s.csv"), "mode": "update"})
    page = amy.html("/collections/1"); assert "Fabric" in page and "Poplin" in page and "<strong>3.5</strong>" in page and "Width" in page

def test_import_requires_edit_rights_and_a_name_column(env, amy, bob):
    seed(amy); assert bob.get("/collections/1/import").status_code == 404
    assert "Name column" in amy.post("/collections/1/import", {"file": (io.BytesIO(b"a,b\n1,2\n"), "s.csv")}, follow_redirects=True).get_data(as_text=True)

def test_export_neutralises_spreadsheet_formulas(env, amy):
    seed(amy); amy.post("/types/1/items/new", {"name": "=HYPERLINK(\"http://evil\")", "qty": "1"})
    assert "'=HYPERLINK" in amy.get("/collections/1/export.csv").get_data(as_text=True)
