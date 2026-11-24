"""Admin command line. Run inside the container:  docker exec -it craft-stash python manage.py <command>"""
import argparse, getpass, os, re, sqlite3, time
from pathlib import Path
from werkzeug.security import generate_password_hash
import backup, migrations

DATA = Path(os.environ.get("DATA_DIR", "/data"))

def conn():
    c = sqlite3.connect(DATA / "craft.db"); c.row_factory = sqlite3.Row; return c

def reset_password(a):
    c = conn(); u = c.execute("SELECT id FROM users WHERE username=?", (a.username,)).fetchone()
    if not u: raise SystemExit(f"No user called {a.username}.")
    pw = a.password or getpass.getpass("New password: ")
    if len(pw) < 8: raise SystemExit("Use 8 or more characters.")
    c.execute("UPDATE users SET pw=?, active=1 WHERE id=?", (generate_password_hash(pw), u["id"])); c.commit()
    print(f"Password for {a.username} updated (they are signed out everywhere).")

def create_user(a):
    migrations.run(DATA / "craft.db", (Path(__file__).parent / "schema.sql").read_text())   # works before the first start too
    if not re.fullmatch(r"[\w.-]{3,40}", a.username): raise SystemExit("Username: 3-40 letters, numbers, dots, dashes or underscores.")
    pw = a.password or getpass.getpass("Password: ")
    if len(pw) < 8: raise SystemExit("Use 8 or more characters.")
    c = conn()
    try: c.execute("INSERT INTO users(username,pw,is_admin) VALUES(?,?,?)", (a.username, generate_password_hash(pw), int(a.admin))); c.commit()
    except sqlite3.IntegrityError: raise SystemExit(f"{a.username} already exists.")
    print(f"Created {'admin ' if a.admin else ''}{a.username}.")

def make_admin(a):
    c = conn(); n = c.execute("UPDATE users SET is_admin=1, active=1 WHERE username=?", (a.username,)).rowcount; c.commit()
    print("Done." if n else f"No user called {a.username}.")

def list_users(a):
    for u in conn().execute("SELECT username, is_admin, active FROM users ORDER BY username"):
        print(f"{u['username']:<24}{'admin' if u['is_admin'] else '':<8}{'' if u['active'] else 'disabled'}")

def restore(a):
    backup.restore(DATA, a.file, force=a.force); print("Restored. Start the app again.")

def cleanup(a):
    c = conn(); used = {r[0] for r in c.execute("SELECT stored FROM files")} | {r[0] for r in c.execute("SELECT photo FROM items WHERE photo<>''")}
    n = 0
    for p in (DATA / "uploads").rglob("*"):
        if p.is_file() and p.name not in used and time.time() - p.stat().st_mtime > 3600:
            print(("would delete " if a.dry_run else "deleting ") + str(p.relative_to(DATA))); n += 1
            if not a.dry_run: p.unlink()
    print(f"{n} orphaned file(s).")

p = argparse.ArgumentParser(description=__doc__); s = p.add_subparsers(dest="cmd", required=True)
x = s.add_parser("reset-password", help="set a user's password (also re-enables the account)"); x.add_argument("username"); x.add_argument("--password", help="avoid: visible in shell history; leave out to be asked"); x.set_defaults(f=reset_password)
x = s.add_parser("create-user", help="create an account (asks for the password)"); x.add_argument("username"); x.add_argument("--admin", action="store_true"); x.add_argument("--password", help=argparse.SUPPRESS); x.set_defaults(f=create_user)
x = s.add_parser("make-admin", help="make a user an admin"); x.add_argument("username"); x.set_defaults(f=make_admin)
x = s.add_parser("list-users", help="list users"); x.set_defaults(f=list_users)
x = s.add_parser("restore", help="restore from a backup zip or database snapshot (stop the app first)"); x.add_argument("file"); x.add_argument("--force", action="store_true", help="restore even though the app seems to be running"); x.set_defaults(f=restore)
x = s.add_parser("cleanup", help="delete uploaded files nothing refers to"); x.add_argument("--dry-run", action="store_true"); x.set_defaults(f=cleanup)
if __name__ == "__main__":
    a = p.parse_args(); a.f(a)
