"""The database layer: SQLite by default, or an external PostgreSQL or MariaDB/MySQL server.

The app writes SQLite-flavoured SQL with `?` placeholders. On SQLite it runs as is (a plain sqlite3 connection). On
other engines a thin wrapper gives the same interface (execute/executemany/commit/rollback/close, rows by name and
by position, cursor.lastrowid) and translates the few things that differ:

    ?                   -> %s (bound client-side by the driver, safely quoted)
    INSERT OR IGNORE    -> INSERT ... ON CONFLICT DO NOTHING
    INSERT INTO <t>     -> ... RETURNING id, so cursor.lastrowid works
    COLLATE NOCASE      -> Postgres: the "nocase" collation the schema creates; MySQL: utf8mb4_unicode_ci
    ON CONFLICT ... DO UPDATE SET c=excluded.c   -> MySQL: ON DUPLICATE KEY UPDATE c=VALUES(c)
    key, stored         -> MySQL: `key`, `stored` (reserved words there)

Anything else that differs goes through the small helpers at the bottom (like_op, json_get, json_has, json_drop,
upsert, insert_ignore, greatest, ci, now), which return the right SQL for the engine in use.

Choosing the engine (first match wins):
    DATABASE_URL       postgresql://user:pass@host:5432/craftstash  or  mysql://user:pass@host:3306/craftstash
    DB_TYPE=postgres   (or mysql / mariadb) with DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD (or DB_PASSWORD_FILE), DB_SSLMODE
    (nothing)          SQLite at DATA_DIR/craft.db
"""
import os, re, sqlite3, time
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

ENGINES = ("sqlite", "postgres", "mysql")
# Tables whose rows have an integer id, so an INSERT can hand it back as cursor.lastrowid.
ID_TABLES = {"users", "pools", "types", "items", "projects", "assignments", "files", "steps", "activity", "step_photos",
             "item_places", "api_tokens", "user_identities"}
# Every table, parents before children (the order a dump is loaded in).
TABLES = ["users", "settings", "pools", "types", "items", "item_places", "projects", "assignments", "files", "steps",
          "step_photos", "pool_shares", "project_shares", "project_tags", "activity", "api_tokens", "user_identities"]

def secret(name, default=""):
    """An environment value, or the contents of the file named by NAME_FILE (Docker secrets)."""
    f = os.environ.get(name + "_FILE", "").strip()
    if f:
        try: return Path(f).read_text().strip()
        except OSError as e: raise SystemExit(f"{name}_FILE={f} can't be read ({e.strerror}).")
    return os.environ.get(name, default)

class Config:
    def __init__(self, engine, url=None, sqlite_path=None, connect_timeout=60):
        self.engine, self.url, self.sqlite_path, self.connect_timeout = engine, url, sqlite_path, connect_timeout

    @property
    def external(self): return self.engine != "sqlite"

    def describe(self):
        """For logs: the engine and where, never the password."""
        if not self.external: return f"sqlite ({self.sqlite_path})"
        u = urlsplit(self.url)
        return f"{self.engine} ({u.username or ''}@{u.hostname or ''}:{u.port or ''}{u.path})"

def _engine_of(scheme):
    s = scheme.split("+")[0].lower()
    if s in ("postgres", "postgresql", "pg"): return "postgres"
    if s in ("mysql", "mariadb"): return "mysql"
    if s == "sqlite": return "sqlite"
    raise SystemExit(f"DATABASE_URL: unknown database type '{scheme}'. Use postgresql://, mysql:// or mariadb://.")

def config_from_env(sqlite_path):
    timeout = int(os.environ.get("DB_CONNECT_TIMEOUT", "60") or 60)
    url = secret("DATABASE_URL").strip()
    if not url and os.environ.get("DB_TYPE", "").strip().lower() not in ("", "sqlite"):
        kind = _engine_of(os.environ["DB_TYPE"].strip())
        host = os.environ.get("DB_HOST", "").strip() or ("postgres" if kind == "postgres" else "mariadb")
        port = os.environ.get("DB_PORT", "").strip() or ("5432" if kind == "postgres" else "3306")
        # Empty counts as unset: compose files pass every variable, set or not.
        user, name = os.environ.get("DB_USER", "").strip() or "craftstash", os.environ.get("DB_NAME", "").strip() or "craftstash"
        pw = secret("DB_PASSWORD")
        url = f"{'postgresql' if kind == 'postgres' else 'mysql'}://{quote(user, safe='')}:{quote(pw, safe='')}@{host}:{port}/{quote(name, safe='')}"
    if not url: return Config("sqlite", sqlite_path=Path(sqlite_path), connect_timeout=timeout)
    engine = _engine_of(urlsplit(url).scheme)
    if engine == "sqlite": return Config("sqlite", sqlite_path=Path(urlsplit(url).path or sqlite_path), connect_timeout=timeout)
    ssl = os.environ.get("DB_SSLMODE", "").strip()
    if ssl and "sslmode=" not in url: url += ("&" if "?" in url else "?") + "sslmode=" + quote(ssl)
    return Config(engine, url=url, connect_timeout=timeout)

def config_from_env_url(url, sqlite_path):
    """A config for an explicit URL (manage.py move-db --to ...), with the same timeout and SSL settings."""
    saved = {k: os.environ.pop(k, None) for k in ("DATABASE_URL", "DATABASE_URL_FILE", "DB_TYPE")}
    try:
        os.environ["DATABASE_URL"] = url
        return config_from_env(sqlite_path)
    finally:
        os.environ.pop("DATABASE_URL", None)
        for k, v in saved.items():
            if v is not None: os.environ[k] = v

CFG = None
def configure(cfg):
    global CFG; CFG = cfg; return cfg

def engine(): return CFG.engine if CFG else "sqlite"

# ---------------------------------------------------------------- errors
IntegrityError = (sqlite3.IntegrityError,)
DatabaseError = (sqlite3.DatabaseError,)
OperationalError = (sqlite3.OperationalError,)
try:
    import psycopg
    IntegrityError += (psycopg.IntegrityError,); DatabaseError += (psycopg.DatabaseError,); OperationalError += (psycopg.OperationalError,)
except ImportError: psycopg = None
try:
    import pymysql
    IntegrityError += (pymysql.err.IntegrityError,); DatabaseError += (pymysql.err.DatabaseError,); OperationalError += (pymysql.err.OperationalError,)
except ImportError: pymysql = None

# ---------------------------------------------------------------- rows and cursors
from decimal import Decimal
def _plain(v):
    """MySQL returns SUM()/COUNT() over integers as Decimal; SQLite and Postgres give int or float."""
    if isinstance(v, Decimal): return int(v) if v == v.to_integral_value() else float(v)
    return v

class Row:
    """Like sqlite3.Row: by name or by position, keys(), dict(row), tuple(row)."""
    __slots__ = ("_v", "_k")
    def __init__(self, values, keys): self._v, self._k = tuple(_plain(v) for v in values), keys
    def __getitem__(self, i): return self._v[self._k[i]] if isinstance(i, str) else self._v[i]
    def __len__(self): return len(self._v)
    def __iter__(self): return iter(self._v)
    def keys(self): return list(self._k)
    def __eq__(self, o):
        if isinstance(o, tuple): return self._v == o
        return isinstance(o, Row) and self._v == o._v and self._k == o._k
    def __hash__(self): return hash(self._v)
    def __repr__(self): return "Row(" + ", ".join(f"{k}={v!r}" for k, v in zip(self._k, self._v)) + ")"

class Cursor:
    def __init__(self, cur, returning=False):
        self._c = cur; self.rowcount = cur.rowcount; self._keys = None
        self.lastrowid = getattr(cur, "lastrowid", None) or None        # MySQL: the driver knows it
        if returning:
            rows = cur.fetchall() if cur.description else []
            self.lastrowid = rows[-1][0] if rows else None
    def _k(self):
        if self._keys is None: self._keys = {d[0]: n for n, d in enumerate(self._c.description or [])}
        return self._keys
    def fetchone(self):
        r = self._c.fetchone() if self._c.description else None
        return None if r is None else Row(r, self._k())
    def fetchall(self):
        if not self._c.description: return []
        k = self._k(); return [Row(r, k) for r in self._c.fetchall()]
    def __iter__(self): return iter(self.fetchall())
    @property
    def description(self): return self._c.description

# ---------------------------------------------------------------- SQL translation
_INSERT = re.compile(r"^\s*INSERT\s+(OR\s+IGNORE\s+)?INTO\s+(\w+)", re.I)

def _segments(sql, backslash=False):
    """Split sql into (kind, text) pieces: kind is "code", "literal" ('...', "...", `...`) or "comment" (-- to the end
    of the line, /* ... */). A doubled quote inside a literal ('it''s') stays in that literal; with backslash=True (MySQL's
    default) so does a backslash-escaped one ('it\\'s'). An unterminated literal or comment runs to the end."""
    out, i, n, start = [], 0, len(sql), 0
    def flush(j, kind="code"):
        if j > start: out.append((kind, sql[start:j]))
    while i < n:
        ch = sql[i]
        if ch in "'\"`":
            flush(i); j = i + 1
            while j < n:
                if backslash and ch != "`" and sql[j] == "\\": j += 2; continue
                if sql[j] == ch:
                    if j + 1 < n and sql[j + 1] == ch: j += 2; continue
                    break
                j += 1
            start = i; i = min(j + 1, n); flush(i, "literal"); start = i
        elif sql.startswith("--", i) or sql.startswith("/*", i):
            flush(i); j = sql.find("\n", i) if ch == "-" else sql.find("*/", i + 2)
            j = n if j < 0 else (j if ch == "-" else j + 2)
            start = i; i = j; flush(i, "comment"); start = i
        else: i += 1
    flush(n)
    return out

def _code(sql, fn, backslash=False):
    """Apply fn to the SQL code only, leaving literals and comments as they are."""
    return "".join(fn(t) if k == "code" else t for k, t in _segments(sql, backslash))

def _code_only(sql):
    """sql with literals and comments blanked out, for searching keywords."""
    return " ".join(t for k, t in _segments(sql) if k == "code")

def placeholders(sql, has_params, backslash=False):
    """`?` in code -> %s and `:name` -> %(name)s; every % doubled, in literals and comments too (the drivers treat % as a
    format character when there are params). `::type` casts are left alone; `?` in a literal or comment is not a placeholder."""
    if not has_params: return sql
    def conv(part):
        out, i, n = [], 0, len(part)
        while i < n:
            ch = part[i]
            if ch == "?": out.append("%s"); i += 1; continue
            if ch == ":" and i + 1 < n and (part[i + 1].isalpha() or part[i + 1] == "_") and (i == 0 or part[i - 1] != ":"):
                j = i + 1
                while j < n and (part[j].isalnum() or part[j] == "_"): j += 1
                out.append(f"%({part[i + 1:j]})s"); i = j; continue
            out.append("%%" if ch == "%" else ch); i += 1
        return "".join(out)
    return "".join(conv(t) if k == "code" else t.replace("%", "%%") for k, t in _segments(sql, backslash))

_RESERVED_MYSQL = re.compile(r"(?<![`\w.:])(key|stored)(?![`\w])|(?<=\.)(key|stored)(?![`\w])", re.I)

def _mysql(sql):
    def fix(part):
        part = _RESERVED_MYSQL.sub(lambda m: f"`{m.group(1) or m.group(2)}`", part)
        part = re.sub(r"\bCOLLATE\s+NOCASE\b", "COLLATE utf8mb4_unicode_ci", part, flags=re.I)
        part = re.sub(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", "INSERT IGNORE INTO", part, flags=re.I)
        part = re.sub(r"\bON\s+CONFLICT\s*\([^)]*\)\s*DO\s+UPDATE\s+SET\b", "ON DUPLICATE KEY UPDATE", part, flags=re.I)
        part = re.sub(r"\bON\s+CONFLICT\s*(\([^)]*\))?\s*DO\s+NOTHING\b", "", part, flags=re.I)
        return re.sub(r"\bexcluded\.(\w+)", r"VALUES(\1)", part, flags=re.I)
    return _code(sql, fix, backslash=True)

def translate(sql, eng, has_params=True):
    """SQLite-flavoured SQL -> the engine's. Returns (sql, adds_returning)."""
    returning = False
    if eng == "mysql": return placeholders(_mysql(sql), has_params, backslash=True), False
    m = _INSERT.match(sql)
    if eng == "postgres" and m:
        code = _code_only(sql)
        def add(sql, clause):
            sql = sql.rstrip().rstrip(";").rstrip()
            return sql + ("\n" if _segments(sql)[-1][0] == "comment" else "") + clause   # not inside a trailing -- comment
        if m.group(1):
            sql = sql[:m.start(1)] + sql[m.end(1):]
            if not re.search(r"\bON\s+CONFLICT\b", code, re.I): sql = add(sql, " ON CONFLICT DO NOTHING")
        if m.group(2).lower() in ID_TABLES and not re.search(r"\bRETURNING\b", code, re.I):
            sql = add(sql, " RETURNING id"); returning = True
    return placeholders(sql, has_params), returning

class Conn:
    """A sqlite3.Connection look-alike over a server database (DB-API driver connection underneath)."""
    def __init__(self, raw, eng):
        self.raw, self.engine, self._dirty = raw, eng, False

    def _run(self, cur, sql, params):
        has = bool(params)
        tsql, returning = translate(sql, self.engine, has)
        write = not re.match(r"^\s*(SELECT|WITH)\b", sql, re.I)
        # Postgres aborts the whole transaction on any error, SQLite only the statement. A savepoint around each write
        # in a transaction that already changed something keeps SQLite's behaviour (e.g. a duplicate username caught
        # by the app doesn't lose earlier work in the same request).
        sp = self.engine == "postgres" and self._dirty
        try:
            if sp: cur.execute("SAVEPOINT dbx")
            cur.execute(tsql, (dict(params) if hasattr(params, "keys") else tuple(params)) if has else None)
        except Exception:
            if sp:
                try: self.raw.cursor().execute("ROLLBACK TO SAVEPOINT dbx")
                except Exception: pass
            elif self.engine == "postgres":
                try: self.raw.rollback()
                except Exception: pass
            raise
        out = Cursor(cur, returning)
        if sp: self.raw.cursor().execute("RELEASE SAVEPOINT dbx")
        if write: self._dirty = True
        return out

    def execute(self, sql, params=()):
        return self._run(self._cursor(), sql, params)

    def executemany(self, sql, seq):
        seq = [tuple(p) for p in seq]
        if not seq: return None
        tsql, _ = translate(sql, self.engine, True)
        tsql = re.sub(r"\s+RETURNING id$", "", tsql)
        cur = self._cursor(); cur.executemany(tsql, seq); self._dirty = True
        return Cursor(cur)

    def _cursor(self):
        return self.raw.cursor()

    def commit(self): self.raw.commit(); self._dirty = False
    def rollback(self): self.raw.rollback(); self._dirty = False
    def close(self):
        try: self.raw.close()
        except Exception: pass
    @property
    def in_transaction(self): return self._dirty
    def __enter__(self): return self
    def __exit__(self, et, *_):
        if et: self.rollback()
        else: self.commit()

def connect(cfg=None, wait=False):
    """A connection to the configured database. wait=True keeps retrying for DB_CONNECT_TIMEOUT seconds (startup)."""
    cfg = cfg or CFG
    if cfg is None or not cfg.external:
        c = sqlite3.connect(cfg.sqlite_path if cfg else "craft.db", timeout=30)
        c.row_factory = sqlite3.Row; c.execute("PRAGMA foreign_keys=ON")
        return c
    end = time.time() + (cfg.connect_timeout if wait else 0)
    while True:
        try: return _connect_server(cfg)
        except OperationalError as e:
            if time.time() >= end: raise SystemExit(f"Can't connect to the database {cfg.describe()}: {str(e).strip()}")
            print(f"[db] waiting for {cfg.describe()}: {str(e).strip().splitlines()[0] if str(e).strip() else e}", flush=True)
            time.sleep(2)

def mysql_args(url, multi=False):
    """PyMySQL connect() arguments from a mysql:// URL (?sslmode=require|verify-full turns on TLS)."""
    u = urlsplit(url); q = dict(p.split("=", 1) for p in u.query.split("&") if "=" in p)
    args = dict(host=u.hostname or "localhost", port=u.port or 3306, user=unquote(u.username or ""), password=unquote(u.password or ""),
                database=unquote(u.path.lstrip("/")), charset="utf8mb4", autocommit=False, connect_timeout=10,
                init_command="SET SESSION sql_mode=CONCAT(@@sql_mode, ',STRICT_TRANS_TABLES'), time_zone='+00:00'")
    mode = q.get("sslmode", "")
    if mode in ("require", "verify-ca", "verify-full"):
        import ssl
        ctx = ssl.create_default_context()
        if mode == "require": ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
        args["ssl"] = ctx
    if multi: args["client_flag"] = pymysql.constants.CLIENT.MULTI_STATEMENTS
    return args

def _connect_server(cfg):
    if cfg.engine == "postgres":
        if psycopg is None: raise SystemExit("PostgreSQL needs the psycopg package (it is in the Docker image).")
        url = re.sub(r"^\w+(\+\w+)?://", "postgresql://", cfg.url)
        raw = psycopg.connect(url, cursor_factory=psycopg.ClientCursor, connect_timeout=10)
        return Conn(raw, "postgres")
    if cfg.engine == "mysql":
        if pymysql is None: raise SystemExit("MariaDB/MySQL needs the PyMySQL package (it is in the Docker image).")
        return Conn(pymysql.connect(**mysql_args(cfg.url)), "mysql")
    raise SystemExit(f"Database type {cfg.engine} isn't supported yet.")

# ---------------------------------------------------------------- engine-specific SQL helpers
# These build SQL text, so everything they put into it is checked: identifiers against IDENT, small expressions
# against EXPR, JSON keys against JSON_KEY. Values always travel as bound parameters, never in the text.
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?")
EXPR = re.compile(r"[A-Za-z0-9_.?+\-* ]{1,80}")
JSON_KEY = re.compile(r"[A-Za-z0-9_-]{1,64}")

def ident(name):
    if not isinstance(name, str) or not IDENT.fullmatch(name): raise ValueError(f"not a plain SQL identifier: {name!r}")
    return name

def expr(text):
    if not isinstance(text, str) or not EXPR.fullmatch(text): raise ValueError(f"not a simple SQL expression: {text!r}")
    return text

def like_op():
    """`<expr> LIKE ?` that ignores case and treats ! as the escape character, on every engine."""
    if engine() == "postgres": return """COLLATE "default" ILIKE ? ESCAPE '!'"""
    if engine() == "mysql": return "COLLATE utf8mb4_unicode_ci LIKE ? ESCAPE '!'"
    return "LIKE ? ESCAPE '!'"

def ci(e):
    """An expression that sorts and compares ignoring case."""
    return f"{ident(e)} COLLATE NOCASE"

def json_key(key):
    """The parameter that names one key of a JSON object column, for json_get and json_drop."""
    key = str(key)
    if not JSON_KEY.fullmatch(key): raise ValueError(f"not a field id: {key!r}")
    return key if engine() == "postgres" else '$."' + key + '"'

def json_get(col):
    """SQL for one value (as text) of a JSON object column; takes one parameter, json_key(...)."""
    ident(col)
    if engine() == "postgres": return f"(({col})::jsonb ->> ?)"
    if engine() == "mysql": return f"JSON_UNQUOTE(JSON_EXTRACT({col}, ?))"
    return f"json_extract({col}, ?)"

def json_has(col, op):
    """SQL that is true when any value of a JSON object column matches `op` (e.g. like_op()), which takes the parameters."""
    ident(col)
    if op != like_op(): raise ValueError("json_has takes like_op()")
    if engine() == "postgres": return f"EXISTS(SELECT 1 FROM jsonb_each_text(({col})::jsonb) AS je WHERE je.value {op})"
    if engine() == "mysql":
        return f"EXISTS(SELECT 1 FROM JSON_TABLE({col}, '$.*' COLUMNS(value TEXT PATH '$')) AS je WHERE je.value {op})"
    return f"EXISTS(SELECT 1 FROM json_each({col}) WHERE value {op})"

def json_drop(col):
    """SQL for a JSON object column without one key; takes one parameter, json_key(...)."""
    ident(col)
    if engine() == "postgres": return f"((({col})::jsonb - ?)::text)"
    if engine() == "mysql": return f"JSON_REMOVE({col}, ?)"
    return f"json_remove({col}, ?)"

def group_concat_distinct(col):
    """The distinct values of a column joined with commas (NULL when there are none)."""
    ident(col)
    if engine() == "postgres": return f"string_agg(DISTINCT {col}, ',')"
    if engine() == "mysql": return f"GROUP_CONCAT(DISTINCT {col} SEPARATOR ',')"
    return f"group_concat(DISTINCT {col})"

def greatest(a, b):
    expr(a); expr(b)
    return f"MAX({a}, {b})" if engine() == "sqlite" else f"GREATEST({a}, {b})"

def upsert(table, cols, keys, update):
    """INSERT ... that updates on a duplicate key. update maps column -> expression, where excluded.<col> is the new value."""
    ident(table); [ident(c) for c in [*cols, *keys, *update]]; [expr(e) for e in update.values()]
    vals = ",".join("?" * len(cols))
    sets = ", ".join(f"{c}={e}" for c, e in update.items())
    return f"INSERT INTO {table}({','.join(cols)}) VALUES({vals}) ON CONFLICT({','.join(keys)}) DO UPDATE SET {sets}"

def insert_ignore(table, cols):
    ident(table); [ident(c) for c in cols]
    return f"INSERT OR IGNORE INTO {table}({','.join(cols)}) VALUES({','.join('?' * len(cols))})"

def now():
    """The current UTC time in the text format every timestamp column uses."""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())

def ago(days):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() - days * 86400))

def fit_places(c, iid, total=None):
    """An item's other places never hold more than its total: when they would, they shrink in proportion. The app
    calls this wherever qty can drop; the places_fit trigger in each schema is the backstop."""
    if total is None:
        r = c.execute("SELECT qty FROM items WHERE id=?", (iid,)).fetchone()
        if not r: return
        total = r[0]
    held = c.execute("SELECT COALESCE(SUM(qty), 0) FROM item_places WHERE item_id=?", (iid,)).fetchone()[0]
    if total < held - 1e-12:
        c.execute("UPDATE item_places SET qty=qty*?/? WHERE item_id=?", (total, held, iid))
        c.execute("DELETE FROM item_places WHERE item_id=? AND qty<1e-9", (iid,))
