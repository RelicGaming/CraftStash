"""SQL injection: a static audit of every query in app/, and hostile input through every way in."""
import ast, io, json, urllib.parse
from pathlib import Path
import pytest
from conftest import seed

APP = Path(__file__).resolve().parent.parent / "app"
SQL_CALLS = {"execute", "executemany", "q", "get", "run"}

# Everything allowed to appear inside the text of a query that isn't a literal. Each is a constant, an allowlist
# lookup, or a dbx helper that checks what it puts into SQL. Values never go in the text: they are bound parameters.
SAFE_NAMES = {
    "LIKE": "dbx.like_op(): a fixed operator per engine",
    "GROUP_HEX": "dbx.group_concat_distinct('s.hex'), checked identifier",
    "LOW": "module constant", "CAN_EDIT": "module constant", "SEE_WISH": "module constant", "EDIT_WISH": "module constant",
    "WISH_SELECT": "module constant", "SET_SETTING": "dbx.upsert() with fixed names",
    "cond": "match(): built from LIKE, dbx.json_get on a fixed column, and ? placeholders",
    "sql": "built from string literals, match() and SORTS[...] in the same function",
    "t": "a table from dbx.TABLES (fixed list)", "table": "migrations: fixed table names from STEPS",
    "col": "migrations: fixed column names from STEPS; patterns.stash: s.pool_id or s.type_id from a fixed dict",
    "marks": "patterns.legend: '?,?,?', one placeholder per id", "ddl": "migrations: fixed column types from STEPS",
    "order": "dump: ' ORDER BY id' or ''",
    "cols": "dump: column names read from the database itself (and, when loading, only those the target table has)", "tsql": "dbx: the translated text of a checked query",
    "st": "schema statements from the schema files", "MYSQL_TRIGGER": "constant",
}
SAFE_CALLS = {  # function -> why its result is safe in SQL text
    "dbx.json_get": "identifier checked", "dbx.json_drop": "identifier checked", "dbx.json_has": "identifier and operator checked",
    "dbx.greatest": "expressions checked", "dbx.upsert": "identifiers and expressions checked",
    "dbx.insert_ignore": "identifiers checked", "dbx.group_concat_distinct": "identifier checked", "dbx.ci": "identifier checked",
    "column": "importer.ITEM_COLUMNS allowlist", "map": "map(column, ...) in the importer", "len": "a count",
    "schema_file": "the schema file for the engine",
}
SAFE_SUBSCRIPTS = {"SORTS", "FILE_SCOPES", "PHOTO_SCOPES", "STEP_PHOTO_SCOPES", "PROJECT_PHOTO_SCOPES"}   # frozen allowlists

def name_of(node):
    if isinstance(node, ast.Name): return node.id
    if isinstance(node, ast.Attribute): return f"{name_of(node.value)}.{node.attr}"
    return ""

def unsafe_parts(node):
    """Pieces of a query's text that aren't literals or known-safe, as source snippets."""
    if isinstance(node, ast.Constant): return []
    if isinstance(node, ast.JoinedStr): return [p for v in node.values for p in unsafe_parts(v)]
    if isinstance(node, ast.FormattedValue): return unsafe_parts(node.value)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mult)): return unsafe_parts(node.left) + unsafe_parts(node.right)
    if isinstance(node, ast.IfExp): return unsafe_parts(node.body) + unsafe_parts(node.orelse)   # the test only picks one
    if isinstance(node, ast.Name) and node.id in SAFE_NAMES: return []
    if isinstance(node, ast.Subscript) and name_of(node.value) in SAFE_SUBSCRIPTS: return []
    if isinstance(node, ast.Call):
        fn = name_of(node.func)
        if fn in SAFE_CALLS: return []
        if isinstance(node.func, ast.Attribute) and node.func.attr == "join" and isinstance(node.func.value, ast.Constant):
            return [p for a in node.args for p in unsafe_parts(a)]       # ",".join(<safe parts>)
        if fn in ("str", "int") and node.args and isinstance(node.args[0], ast.Call) and name_of(node.args[0].func) == "len": return []
    if isinstance(node, (ast.GeneratorExp, ast.ListComp)):
        return unsafe_parts(node.elt)
    if isinstance(node, ast.Call) and name_of(node.func) == "int": return []                      # f"KILL {int(pid)}"
    return [ast.unparse(node)]

def test_every_query_in_the_app_is_parameterised():
    problems = []
    for path in sorted(APP.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args: continue
            fn = node.func.attr if isinstance(node.func, ast.Attribute) else name_of(node.func)
            if fn not in SQL_CALLS: continue
            if fn in ("get", "run", "q") and isinstance(node.func, ast.Attribute): continue   # dict.get(), etc.
            if path.name == "selftest.py": continue                                          # talks HTTP, not SQL
            arg = node.args[0]
            if fn == "run" and isinstance(arg, ast.Attribute): continue                       # migrations.run(path, ...)
            if fn == "get" and not (isinstance(arg, (ast.Constant, ast.JoinedStr, ast.BinOp)) and "SELECT" in ast.unparse(arg)): continue
            for part in unsafe_parts(arg):
                problems.append(f"{path.name}:{node.lineno}: {part}")
    assert not problems, "Query text built from something not known to be safe:\n" + "\n".join(problems)

def test_helpers_refuse_anything_but_identifiers():
    import dbx
    for bad in ("name; DROP TABLE users", "a b", "x'--", "1=1", "", "id)", "users.id.x"):
        with pytest.raises(ValueError): dbx.ident(bad)
        with pytest.raises(ValueError): dbx.upsert("settings", [bad], ["key"], {"value": "excluded.value"})
    with pytest.raises(ValueError): dbx.greatest("0", "qty); DROP TABLE users; --")
    for bad in ('f1" OR 1=1', "f1') --", "a.b", "$.x", ""):
        with pytest.raises(ValueError): dbx.json_key(bad)
    import importer
    with pytest.raises(ValueError): importer.column("name=(SELECT pw FROM users)")

# ---------------------------------------------------------------- hostile input through the front door
PAYLOADS = ["' OR '1'='1", "x'; DROP TABLE users; --", '" OR 1=1 --', "1) OR (1=1", "Robert'); DELETE FROM items;--",
            "' UNION SELECT pw, pw, pw FROM users --", "%", "_", "!", "\\", "$.\"f0\"", "') OR json_extract(extra,'$') --"]

def intact(env, users=None):
    d = env.db()
    n = d.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    assert users is None or n == users
    assert d.execute("SELECT COUNT(*) FROM items").fetchone()[0] >= 0                      # table still there
    return n

def test_login_and_register_with_hostile_names(env, amy):
    c = env.client()
    for p in PAYLOADS:
        r = c.post("/login", {"username": p, "password": p}); assert r.status_code in (200, 302)
        assert c.get("/").status_code == 302                                                 # still not logged in
        assert c.post("/register", {"username": p, "password": "password1"}).status_code in (200, 302)
    intact(env, users=1)

@pytest.mark.parametrize("p", PAYLOADS)
def test_text_is_stored_exactly_as_typed(env, amy, p):
    seed(amy)
    amy.post("/collections", {"name": p})
    amy.post("/types/1/items/new", {"name": p, "colour": p, "notes": p, "location": p, "qty": "2", "place_name": [p + "!"], "place_qty": ["1"]})
    amy.post("/projects/1/update", {"name": p, "status": "active", "notes": p})
    d = env.db()
    assert (p,) in d.execute("SELECT name FROM pools").fetchall()
    assert d.execute("SELECT name, colour, notes, location FROM items WHERE id=3").fetchone() == (p, p, p, p)
    assert d.execute("SELECT name, notes FROM projects WHERE id=1").fetchone() == (p, p)
    intact(env, users=1)

@pytest.mark.parametrize("p", PAYLOADS)
def test_search_and_sort_treat_input_as_text(env, amy, p):
    seed(amy)
    for url in (f"/search?q={urllib.parse.quote(p)}", f"/collections/1?q={urllib.parse.quote(p)}",
                f"/collections/1?sort={urllib.parse.quote(p)}", f"/collections/1?q=Brand:{urllib.parse.quote(p)}",
                f"/api/v1/items?q={urllib.parse.quote(p)}&status={urllib.parse.quote(p)}"):
        r = amy.get(url); assert r.status_code in (200, 401), (url, r.status_code)
    html = amy.html(f"/search?q={urllib.parse.quote(p)}")
    assert "Merino" not in html and "Cotton" not in html                                    # nothing matched by accident
    intact(env, users=1)

def test_wildcards_only_match_literally(env, amy):
    seed(amy); amy.post("/types/1/items/new", {"name": "Wool 50% off", "qty": "1"}); amy.post("/types/1/items/new", {"name": "a_b", "qty": "1"})
    assert "Wool 50% off" in amy.html("/search?q=%25") and "Merino" not in amy.html("/search?q=%25")
    assert "a_b" in amy.html("/search?q=_") and "Merino" not in amy.html("/search?q=_")

def test_api_with_hostile_input(env, amy):
    seed(amy)
    tok = __import__("re").search(r'value="(cs_[\w-]+)"', amy.post("/account/tokens", {"name": "t", "access": "write"}).get_data(as_text=True))[1]
    h = {"Authorization": "Bearer " + tok}
    for p in PAYLOADS:
        assert amy.c.get(f"/api/v1/items?q={urllib.parse.quote(p)}&status={urllib.parse.quote(p)}", headers=h).status_code == 200
        r = amy.c.post("/api/v1/items/1/adjust", json={"action": "use", "amount": 1, "place": p}, headers=h)
        assert r.status_code in (200, 400, 404)
        assert amy.c.post("/api/v1/shopping/restock", json={"items": [{"id": p, "amount": p}]}, headers=h).status_code in (200, 400)
        assert amy.c.get("/api/v1/items/" + urllib.parse.quote(p), headers=h).status_code == 404
        # A wrong token counts towards that address's limit, so these come from elsewhere.
        assert amy.c.get("/api/v1", headers={"Authorization": "Bearer " + p}, environ_overrides={"REMOTE_ADDR": "198.51.100.7"}).status_code in (401, 429)
    intact(env, users=1)

def test_hostile_csv_import(env, amy):
    seed(amy)
    rows = ["type,name,colour,notes,qty,x'); DROP TABLE users; --"] + [f'Yarn,"{p.replace(chr(34), chr(34) * 2)}",{p.replace(",", "")},n,1,{p.replace(",", "")}' for p in PAYLOADS]
    r = amy.post("/collections/1/import", {"file": (io.BytesIO("\n".join(rows).encode()), "x.csv")}, content_type="multipart/form-data")
    assert r.status_code in (200, 302)
    d = env.db(); names = {n for (n,) in d.execute("SELECT name FROM items")}
    assert "x'; DROP TABLE users; --" in names or "' OR '1'='1" in names
    fields = json.loads(d.execute("SELECT fields FROM types WHERE id=1").fetchone()[0])
    assert any("DROP TABLE" in f["label"] for f in fields)                                    # the header became a field label, nothing more
    intact(env, users=1)

def test_sharing_and_field_ids_with_hostile_input(env, amy, bob):
    seed(amy)
    for p in PAYLOADS:
        assert amy.post("/collections/1/share", {"username": p, "role": "edit"}).status_code in (200, 302)
        assert amy.post("/projects/1/share", {"username": p}).status_code in (200, 302)
        assert amy.post("/types/1/fields/" + urllib.parse.quote(p, safe="") + "/delete").status_code in (302, 404)
    d = env.db()
    assert d.execute("SELECT COUNT(*) FROM pool_shares").fetchone()[0] == 0 and d.execute("SELECT COUNT(*) FROM project_shares").fetchone()[0] == 0
    intact(env, users=2)

@pytest.mark.parametrize("code", [
    """q(f"SELECT * FROM items WHERE name='{request.args['q']}'")""",
    """db().execute("SELECT * FROM items ORDER BY " + request.args.get("sort"))""",
    """run("DELETE FROM items WHERE " + where, 1)""",
    """q(f"SELECT * FROM {tablename}")""",
])
def test_the_audit_catches_unsafe_queries(code):
    call = ast.parse(code).body[0].value
    assert unsafe_parts(call.args[0]), f"the audit missed: {code}"
