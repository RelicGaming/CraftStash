"""Admin command line. Run inside the container:  docker exec -it craft-stash python manage.py <command>"""
import argparse, getpass, json, os, re, sys, time
from pathlib import Path
from werkzeug.security import generate_password_hash
import backup, dbx, dump, migrations, paths

P = paths.Paths(); DATA = P.data

_ready = []
def conn():
    """The configured database (SQLite, or DATABASE_URL), created or upgraded first, so commands work before the first start."""
    if not _ready: _ready.append(migrations.prepare(P, wait=False))
    return dbx.connect()

def reset_password(a):
    c = conn(); u = c.execute("SELECT id FROM users WHERE username=?", (a.username,)).fetchone()
    if not u: raise SystemExit(f"No user called {a.username}.")
    pw = a.password or getpass.getpass("New password: ")
    if len(pw) < 8: raise SystemExit("Use 8 or more characters.")
    c.execute("UPDATE users SET pw=?, active=1 WHERE id=?", (generate_password_hash(pw), u["id"])); c.commit()
    print(f"Password for {a.username} updated (they are signed out everywhere).")

def create_user(a):
    if not re.fullmatch(r"[\w.-]{3,40}", a.username): raise SystemExit("Username: 3-40 letters, numbers, dots, dashes or underscores.")
    pw = a.password or getpass.getpass("Password: ")
    if len(pw) < 8: raise SystemExit("Use 8 or more characters.")
    c = conn()
    try: c.execute("INSERT INTO users(username,pw,is_admin) VALUES(?,?,?)", (a.username, generate_password_hash(pw), int(a.admin))); c.commit()
    except dbx.IntegrityError: raise SystemExit(f"{a.username} already exists.")
    print(f"Created {'admin ' if a.admin else ''}{a.username}.")

def make_admin(a):
    c = conn(); n = c.execute("UPDATE users SET is_admin=1, active=1 WHERE username=?", (a.username,)).rowcount; c.commit()
    print("Done." if n else f"No user called {a.username}.")

def list_users(a):
    for u in conn().execute("SELECT username, is_admin, active FROM users ORDER BY username"):
        print(f"{u['username']:<24}{'admin' if u['is_admin'] else '':<8}{'' if u['active'] else 'disabled'}")

def restore(a):
    conn().close()          # configure the database (SQLite or DATABASE_URL) the backup goes into
    backup.restore(P, a.file, force=a.force); print("Restored. Start the app again.")

def cleanup(a):
    c = conn(); used = ({r[0] for r in c.execute("SELECT stored FROM files")} | {r[0] for r in c.execute("SELECT photo FROM items WHERE photo<>''")}
                        | {r[0] for r in c.execute("SELECT stored FROM step_photos")})      # step photos were missing here before
    n = 0
    for p, _ in backup._upload_files(P):
        if p.name not in used and time.time() - p.stat().st_mtime > 3600:
            print(("would delete " if a.dry_run else "deleting ") + str(p)); n += 1
            if not a.dry_run: p.unlink()
    print(f"{n} orphaned file(s).")

def export_db(a):
    """Every table as portable JSON, from whichever database is configured."""
    c = conn(); data = dump.dump(c); c.close()
    out = sys.stdout if a.file == "-" else open(a.file, "w")
    json.dump(data, out); out.write("\n")
    if out is not sys.stdout: out.close(); print(f"Exported {dump.count(data)} rows to {a.file}.")

def import_db(a):
    """Load a JSON export (or a SQLite craft.db) into the configured database. It must be empty unless --replace."""
    data = dump.read_any(a.file); c = conn()
    try: n = dump.load(c, data, replace=a.replace)
    finally: c.close()
    print(f"Imported {n} rows.")

def move_db(a):
    """Copy everything from the configured database into another one, e.g. from SQLite to PostgreSQL."""
    src = conn(); data = dump.dump(src); src.close()
    target = dbx.config_from_env_url(a.to, P.db)
    if not target.external and target.sqlite_path == dbx.CFG.sqlite_path: raise SystemExit("That is the database you're moving from.")
    t = dbx.connect(target, wait=True)
    try:
        if target.external: migrations.run_server(t)
        else: t.close(); migrations.run(target.sqlite_path, migrations.schema_file("sqlite"), P.backups); t = dbx.connect(target)
        n = dump.load(t, data, replace=a.replace)
    finally: t.close()
    print(f"Copied {n} rows to {target.describe()}. Now set DATABASE_URL to it and start the app.")

p = argparse.ArgumentParser(description=__doc__); s = p.add_subparsers(dest="cmd", required=True)
x = s.add_parser("reset-password", help="set a user's password (also re-enables the account)"); x.add_argument("username"); x.add_argument("--password", help="avoid: visible in shell history; leave out to be asked"); x.set_defaults(f=reset_password)
x = s.add_parser("create-user", help="create an account (asks for the password)"); x.add_argument("username"); x.add_argument("--admin", action="store_true"); x.add_argument("--password", help=argparse.SUPPRESS); x.set_defaults(f=create_user)
x = s.add_parser("make-admin", help="make a user an admin"); x.add_argument("username"); x.set_defaults(f=make_admin)
x = s.add_parser("list-users", help="list users"); x.set_defaults(f=list_users)
x = s.add_parser("restore", help="restore from a backup zip or database snapshot (stop the app first)"); x.add_argument("file"); x.add_argument("--force", action="store_true", help="restore even though the app seems to be running"); x.set_defaults(f=restore)
x = s.add_parser("cleanup", help="delete uploaded files nothing refers to"); x.add_argument("--dry-run", action="store_true"); x.set_defaults(f=cleanup)
x = s.add_parser("export-db", help="export every table as portable JSON (any database)"); x.add_argument("file", help="file to write, or - for stdout"); x.set_defaults(f=export_db)
x = s.add_parser("import-db", help="load a JSON export or a SQLite craft.db into the configured database"); x.add_argument("file"); x.add_argument("--replace", action="store_true", help="delete what is there first"); x.set_defaults(f=import_db)
x = s.add_parser("move-db", help="copy everything into another database (e.g. SQLite to PostgreSQL)"); x.add_argument("--to", required=True, metavar="URL", help="postgresql://user:pass@host:5432/craftstash"); x.add_argument("--replace", action="store_true", help="delete what the target has first"); x.set_defaults(f=move_db)
if __name__ == "__main__":
    a = p.parse_args(); a.f(a)
