"""Photos on project steps."""
import io, sqlite3
from conftest import seed, png_bytes

def db(env): return env.db()
def pic(name="p.png"): return (io.BytesIO(png_bytes()), name)
def stored(env): return [r[0] for r in db(env).execute("SELECT stored FROM step_photos ORDER BY id")]
def files(env): return sorted(p.name for p in (env.data / "uploads" / "photos").iterdir())

def with_step(amy):
    seed(amy); amy.post("/projects/1/steps", {"title": "Cast on", "body": "80 stitches"})

def test_add_photos_to_a_step_and_view_them(env, amy):
    with_step(amy)
    amy.post("/steps/1/photos", {"photos": [pic(), pic()]}, content_type="multipart/form-data")
    names = stored(env); assert len(names) == 2 and files(env) == sorted(names)
    html = amy.html("/projects/1"); assert 'src="/step-photos/1"' in html and 'src="/step-photos/2"' in html
    r = amy.get("/step-photos/1"); assert r.status_code == 200 and r.mimetype == "image/jpeg" and r.data[:2] == b"\xff\xd8"

def test_new_step_can_come_with_photos_only(env, amy):
    seed(amy)
    amy.post("/projects/1/steps", {"title": "", "body": "", "photos": [pic()]}, content_type="multipart/form-data")
    assert db(env).execute("SELECT COUNT(*) FROM steps").fetchone()[0] == 1 and len(stored(env)) == 1
    page = amy.post("/projects/1/steps", {"photos": [(io.BytesIO(b"not an image"), "x.png")]},
                    content_type="multipart/form-data", follow_redirects=True).get_data(as_text=True)
    assert db(env).execute("SELECT COUNT(*) FROM steps").fetchone()[0] == 1 and "be read" in page

def test_photo_limit_per_step(env, amy):
    with_step(amy)
    page = amy.post("/steps/1/photos", {"photos": [pic() for _ in range(8)]}, content_type="multipart/form-data",
                    follow_redirects=True).get_data(as_text=True)
    assert len(stored(env)) == 6 and "up to 6 photos" in page and 'action="/steps/1/photos"' not in page

def test_remove_photo_and_delete_step_or_project_cleans_up_files(env, amy):
    with_step(amy); amy.post("/steps/1/photos", {"photos": [pic(), pic()]}, content_type="multipart/form-data")
    amy.post("/step-photos/1/delete"); assert len(stored(env)) == 1 and len(files(env)) == 1
    amy.post("/steps/1/delete"); assert stored(env) == [] and files(env) == []
    amy.post("/projects/1/steps", {"title": "Again"}); amy.post("/steps/2/photos", {"photos": [pic()]}, content_type="multipart/form-data")
    amy.post("/projects/1/delete"); assert files(env) == []

def test_access_rules(env, amy, bob):
    with_step(amy); amy.post("/steps/1/photos", {"photos": [pic()]}, content_type="multipart/form-data")
    assert bob.get("/step-photos/1").status_code == 404
    amy.post("/projects/1/share", {"username": "bob"})
    assert bob.get("/step-photos/1").status_code == 200
    assert bob.post("/step-photos/1/delete").status_code == 404
    assert bob.post("/steps/1/photos", {"photos": [pic()]}, content_type="multipart/form-data").status_code == 404
    assert len(stored(env)) == 1 and 'action="/step-photos/1/delete"' not in bob.html("/projects/1")

def test_copies_and_templates_bring_their_own_photo_files(env, amy):
    with_step(amy); amy.post("/steps/1/photos", {"photos": [pic()]}, content_type="multipart/form-data")
    amy.post("/projects/1/copy", {"as": "template"}); amy.post("/projects/2/copy")
    assert len(set(stored(env))) == 3 and len(files(env)) == 3
    assert db(env).execute("SELECT s.project_id FROM step_photos sp JOIN steps s ON s.id=sp.step_id ORDER BY sp.id").fetchall() == [(1,), (2,), (3,)]
    amy.post("/projects/1/delete"); assert len(files(env)) == 2 and amy.get("/step-photos/2").status_code == 200

def test_photos_count_towards_the_storage_limit(make_env):
    env = make_env(USER_QUOTA_MB=1); amy = env.client().register("amy"); with_step(amy)
    amy.post("/steps/1/photos", {"photos": [pic()]}, content_type="multipart/form-data")
    size = db(env).execute("SELECT size FROM step_photos").fetchone()[0]
    env.core.QUOTA = size * 2 + size // 2                       # room for one more photo, not two
    page = amy.post("/steps/1/photos", {"photos": [pic(), pic()]}, content_type="multipart/form-data",
                    follow_redirects=True).get_data(as_text=True)
    assert len(stored(env)) == 2 and "storage limit" in page and len(files(env)) == 2
