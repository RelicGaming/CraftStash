"""The database layer: SQLite by default, or an external PostgreSQL server.

The app writes SQLite-flavoured SQL with `?` placeholders. On SQLite it runs as is (a plain sqlite3 connection). On
other engines a thin wrapper gives the same interface (execute/executemany/commit/rollback/close, rows by name and
by position, cursor.lastrowid) and translates the few things that differ:

    ?                   -> %s (bound client-side by the driver, safely quoted)
    INSERT OR IGNORE    -> INSERT ... ON CONFLICT DO NOTHING
    INSERT INTO <t>     -> ... RETURNING id, so cursor.lastrowid works
    COLLATE NOCASE      -> the "nocase" collation the schema creates (case-insensitive, like SQLite's)

Anything else that differs goes through the small helpers at the bottom (like_op, json_get, json_has, json_drop,
upsert, insert_ignore, greatest, ci, now), which return the right SQL for the engine in use.

Choosing the engine (first match wins):
    DATABASE_URL       postgresql://user:pass@host:5432/craftstash
    DB_TYPE=postgres   with DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD (or DB_PASSWORD_FILE), DB_SSLMODE
    (nothing)          SQLite at DATA_DIR/craft.db
"""
import os, re, sqlite3, time
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

ENGINES = ("sqlite", "postgres", "mysql")
# Tables whose rows have an integer id, so an INSERT can hand it back as cursor.lastrowid.
ID_TABLES = {"users", "pools", "types", "items", "projects", "assignments", "files", "steps", "activity", "step_photos",
             "item_places", "api_tokens"}
# Every table, parents before children (the order a dump is loaded in).
TABLES = ["users", "settings", "pools", "types", "items", "item_places", "projects", "assignments", "files", "steps",
          "step_photos", "pool_shares", "project_shares", "activity", "api_tokens"]

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

# ---------------------------------------------------------------- rows and cursors
class Row:
    """Like sqlite3.Row: by name or by position, keys(), dict(row), tuple(row)."""
    __slots__ = ("_v", "_k")
    def __init__(self, values, keys): self._v, self._k = tuple(values), keys
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
        self._c = cur; self.lastrowid = None; self.rowcount = cur.rowcount; self._keys = None; self._pending = None
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

def placeholders(sql, has_params):
    """`?` outside quotes -> %s and `:name` -> %(name)s; every % doubled (the drivers treat % as a format character
    when there are params). `::type` casts are left alone."""
    if not has_params: return sql
    out, quote_ch, i, n = [], None, 0, len(sql)
    while i < n:
        ch = sql[i]
        if quote_ch:
            if ch == quote_ch: quote_ch = None
        elif ch in "'\"": quote_ch = ch
        elif ch == "?": out.append("%s"); i += 1; continue
        elif ch == ":" and i + 1 < n and (sql[i + 1].isalpha() or sql[i + 1] == "_") and (i == 0 or sql[i - 1] != ":"):
            j = i + 1
            while j < n and (sql[j].isalnum() or sql[j] == "_"): j += 1
            out.append(f"%({sql[i + 1:j]})s"); i = j; continue
        out.append("%%" if ch == "%" else ch); i += 1
    return "".join(out)

def translate(sql, eng, has_params=True):
    """SQLite-flavoured SQL -> the engine's. Returns (sql, adds_returning)."""
    returning = False
    m = _INSERT.match(sql)
    if eng == "postgres":
        if m and m.group(1):
            sql = sql[:m.start(1)] + sql[m.end(1):]
            if not re.search(r"\bON\s+CONFLICT\b", sql, re.I): sql = sql.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
        if m and m.group(2).lower() in ID_TABLES and not re.search(r"\bRETURNING\b", sql, re.I):
            sql = sql.rstrip().rstrip(";") + " RETURNING id"; returning = True
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

def _connect_server(cfg):
    if cfg.engine == "postgres":
        if psycopg is None: raise SystemExit("PostgreSQL needs the psycopg package (it is in the Docker image).")
        url = re.sub(r"^\w+(\+\w+)?://", "postgresql://", cfg.url)
        raw = psycopg.connect(url, cursor_factory=psycopg.ClientCursor, connect_timeout=10)
        return Conn(raw, "postgres")
    raise SystemExit(f"Database type {cfg.engine} isn't supported yet.")

# ---------------------------------------------------------------- engine-specific SQL helpers
def like_op():
    """`<expr> LIKE ?` that ignores case and treats ! as the escape character, on every engine."""
    if engine() == "postgres": return """COLLATE "default" ILIKE ? ESCAPE '!'"""
    return "LIKE ? ESCAPE '!'"

def ci(expr):
    """An expression that sorts and compares ignoring case."""
    return f"{expr} COLLATE NOCASE"

def json_key(key):
    """The parameter that names one key of a JSON object column, for json_get and json_drop."""
    key = str(key)
    return key if engine() == "postgres" else '$."' + key.replace('"', "") + '"'

def json_get(col):
    """SQL for one value (as text) of a JSON object column; takes one parameter, json_key(...)."""
    if engine() == "postgres": return f"(({col})::jsonb ->> ?)"
    return f"json_extract({col}, ?)"

def json_has(col, op):
    """SQL that is true when any value of a JSON object column matches `op` (e.g. like_op()), which takes the parameters."""
    if engine() == "postgres": return f"EXISTS(SELECT 1 FROM jsonb_each_text(({col})::jsonb) AS je WHERE je.value {op})"
    return f"EXISTS(SELECT 1 FROM json_each({col}) WHERE value {op})"

def json_drop(col):
    """SQL for a JSON object column without one key; takes one parameter, json_key(...)."""
    if engine() == "postgres": return f"((({col})::jsonb - ?)::text)"
    return f"json_remove({col}, ?)"

def group_concat_distinct(expr):
    """The distinct values of expr joined with commas (NULL when there are none)."""
    return f"string_agg(DISTINCT {expr}, ',')" if engine() == "postgres" else f"group_concat(DISTINCT {expr})"

def greatest(a, b):
    return f"MAX({a}, {b})" if engine() == "sqlite" else f"GREATEST({a}, {b})"

def upsert(table, cols, keys, update):
    """INSERT ... that updates on a duplicate key. update maps column -> expression, where excluded.<col> is the new value."""
    vals = ",".join("?" * len(cols))
    sets = ", ".join(f"{c}={e}" for c, e in update.items())
    return f"INSERT INTO {table}({','.join(cols)}) VALUES({vals}) ON CONFLICT({','.join(keys)}) DO UPDATE SET {sets}"

def insert_ignore(table, cols):
    return f"INSERT OR IGNORE INTO {table}({','.join(cols)}) VALUES({','.join('?' * len(cols))})"

def now():
    """The current UTC time in the text format every timestamp column uses."""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())

def ago(days):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() - days * 86400))
