"""Project tags, dates, and the filter / group / sort bar on /projects (and the same filters in the API)."""
import io, re
import pytest
from conftest import seed, TEST_DB

def sql(env, text, *args):
    c = env.db(); c.execute(text, args); c.commit()

def names(html):
    """Project names shown as tiles, in page order."""
    return re.findall(r'<a class="tile" href="/projects/\d+"><div class="strip">.*?<h3>(.*?)</h3>', html, re.S)

@pytest.fixture
def crafts(env, amy):
    """Hat (active, tags winter+gifts, uses Merino, has a step), Scarf (planned, tag gifts), Bag (planned, no tags), Quilt (done)."""
    seed(amy)                                       # project 1 = Hat
    for n in ("Scarf", "Bag", "Quilt"): amy.post("/projects", {"name": n})
    amy.post("/projects/1/update", {"name": "Hat", "status": "active", "notes": "Slouchy beanie"})
    amy.post("/projects/1/assign", {"item_id": "1", "qty": "2"}); amy.post("/projects/1/steps", {"title": "Cast on", "body": "64 stitches"})
    amy.post("/projects/1/tags", {"tags": "Winter, GIFTS"}); amy.post("/projects/2/tags", {"tags": "gifts"})
    sql(env, "UPDATE projects SET status='done' WHERE id=4")
    return amy

def test_tags_are_cleaned_deduplicated_and_removable(env, amy):
    seed(amy)
    amy.post("/projects/1/tags", {"tags": "  Baby Blanket , GIFTS,gifts, <b>x</b>, ,a" + "b" * 40})
    tags = [r[0] for r in env.db().execute("SELECT tag FROM project_tags ORDER BY tag").fetchall()]
    assert tags == ["a" + "b" * 29, "baby blanket", "bxb", "gifts"]                # lower case, symbols dropped, 30 characters, one gifts
    assert "#baby blanket" in amy.html("/projects/1") and 'value="gifts"' in amy.html("/projects/1")
    amy.post("/projects/1/tags/delete", {"tag": "GIFTS"})
    assert "gifts" not in [r[0] for r in env.db().execute("SELECT tag FROM project_tags").fetchall()]

def test_a_project_holds_at_most_twenty_tags(env, amy):
    seed(amy); amy.post("/projects/1/tags", {"tags": ",".join(f"t{i}" for i in range(25))})
    assert env.db().execute("SELECT COUNT(*) FROM project_tags").fetchone()[0] == 20

def test_only_the_owner_edits_tags_but_sharing_shows_them(env, amy, bob):
    seed(amy); amy.post("/projects/1/tags", {"tags": "gifts"})
    assert bob.post("/projects/1/tags", {"tags": "mine"}).status_code == 404 and bob.post("/projects/1/tags/delete", {"tag": "gifts"}).status_code == 404
    assert "gifts" not in bob.html("/projects")
    amy.post("/projects/1/share", {"username": "bob"})
    page = bob.html("/projects"); assert "Hat" in names(page) and "#gifts" in page and "tag-remove" not in bob.html("/projects/1")
    assert 'action="/projects/1/tags"' not in bob.html("/projects/1")                  # no edit form for a viewer

def test_default_page_is_the_classic_layout(crafts):
    html = crafts.html("/projects")
    assert names(html) == ["Bag", "Hat", "Scarf", "Quilt"]                              # name order, finished last
    assert "New project" in html and "Templates" in html and "Clear" not in html

def test_text_search_covers_name_notes_and_steps(crafts):
    assert names(crafts.html("/projects?q=scarf")) == ["Scarf"]
    assert names(crafts.html("/projects?q=beanie")) == ["Hat"]                          # a note
    assert names(crafts.html("/projects?q=stitches")) == ["Hat"]                        # a step
    assert names(crafts.html("/projects?q=50%25")) == [] and names(crafts.html("/projects?q=%25")) == []   # % is literal, not a wildcard
    assert "No projects match" in crafts.html("/projects?q=zzz")

def test_status_chips_tags_and_shared(crafts, env):
    assert names(crafts.html("/projects?status=active")) == ["Hat"]
    assert names(crafts.html("/projects?status=planned&status=done")) == ["Bag", "Scarf", "Quilt"]
    assert names(crafts.html("/projects?tag=gifts")) == ["Hat", "Scarf"]
    assert names(crafts.html("/projects?tag=gifts&tag=winter")) == ["Hat"]              # all selected tags must match
    assert names(crafts.html("/projects?tag=GIFTS")) == ["Hat", "Scarf"]
    assert names(crafts.html("/projects?shared=1")) == []

def test_shared_chip_and_template_filter(crafts, env, bob):
    crafts.post("/projects/3/copy", {"as": "template"})                                  # "Bag" saved as a template
    crafts.post("/projects/2/share", {"username": "bob"})
    assert names(bob.html("/projects?shared=1")) == ["Scarf"] and names(bob.html("/projects?status=template")) == []
    assert names(crafts.html("/projects?status=template")) == ["Bag"]

def test_uses_collection_filter_only_counts_collections_you_can_see(crafts):
    assert names(crafts.html("/projects?pool=1")) == ["Hat"]
    crafts.post("/collections", {"name": "Sewing"})
    assert names(crafts.html("/projects?pool=2")) == [] and names(crafts.html("/projects?pool=999")) == []

def test_group_by_tag_and_none(crafts):
    html = crafts.html("/projects?group=tag")
    assert [m for m in re.findall(r'<h2 class="section-title">(.*?)</h2>', html) if m != "Templates"] == ["#gifts", "#winter", "Untagged"]
    assert html.count('<h3>Hat</h3>') == 2                                              # under both its tags
    flat = crafts.html("/projects?group=none"); assert "section-title" not in flat and len(names(flat)) == 4
    assert "New project name" in flat                                                    # create form stays reachable

def test_sort_by_recently_updated_and_newest(crafts, env):
    for i in range(1, 5): sql(env, "UPDATE projects SET created=?, updated=? WHERE id=?", f"2026-01-0{i} 00:00:00", f"2026-02-0{5 - i} 00:00:00", i)
    assert names(crafts.html("/projects?group=none&sort=newest")) == ["Quilt", "Bag", "Scarf", "Hat"]
    assert names(crafts.html("/projects?group=none&sort=updated")) == ["Hat", "Scarf", "Bag", "Quilt"]

def test_bad_filter_values_are_ignored(crafts):
    assert "Hat" in crafts.html("/projects?status=nope&group=x&sort=;DROP&pool=abc")
    html = crafts.html("/projects?tag=%3Cscript%3E")                                     # sanitised to the tag "script", which no project has
    assert "<script>" not in html and "#script" in html and names(html) == []

def test_dates_are_set_and_updated(env, amy):
    amy.post("/projects", {"name": "Hat"})
    created, updated = env.db().execute("SELECT created, updated FROM projects").fetchone()
    assert re.match(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$", created) and created == updated
    sql(env, "UPDATE projects SET updated='2000-01-01 00:00:00'")
    amy.post("/projects/1/update", {"name": "Hat 2", "status": "planned"})
    assert env.db().execute("SELECT updated FROM projects").fetchone()[0] > "2000"

def test_copy_and_template_keep_tags(env, amy):
    seed(amy); amy.post("/projects/1/tags", {"tags": "gifts, baby"})
    amy.post("/projects/1/copy", {"as": "template"}); amy.post("/projects/2/copy", {})
    rows = env.db().execute("SELECT project_id, tag FROM project_tags ORDER BY project_id, tag").fetchall()
    assert [tuple(r) for r in rows] == [(1, "baby"), (1, "gifts"), (2, "baby"), (2, "gifts"), (3, "baby"), (3, "gifts")]

def test_deleting_a_project_removes_its_tags(env, amy):
    seed(amy); amy.post("/projects/1/tags", {"tags": "gifts"}); amy.post("/projects/1/delete")
    assert env.db().execute("SELECT COUNT(*) FROM project_tags").fetchone()[0] == 0

def api(c, token, url):
    import json
    return json.loads(c.get(url, headers={"Authorization": "Bearer " + token}).get_data(as_text=True))

def test_api_filters_and_tags(crafts, env):
    import hashlib; tok = "cs_" + "a" * 40
    sql(env, "INSERT INTO api_tokens(user_id,name,hash,can_write) VALUES(1,'t',?,0)", hashlib.sha256(tok.encode()).hexdigest())
    everything = api(crafts, tok, "/api/v1/projects")["projects"]
    assert [p["name"] for p in everything] == ["Bag", "Hat", "Scarf", "Quilt"] and everything[1]["tags"] == ["gifts", "winter"] and everything[1]["updated"]
    assert [p["name"] for p in api(crafts, tok, "/api/v1/projects?tag=gifts&status=active")["projects"]] == ["Hat"]
    assert [p["name"] for p in api(crafts, tok, "/api/v1/projects?q=beanie")["projects"]] == ["Hat"]
    assert api(crafts, tok, "/api/v1/projects?status=bogus")["projects"] == []
    assert api(crafts, tok, "/api/v1/projects/1")["tags"] == ["gifts", "winter"]

def test_tags_survive_export_and_import(env, amy, tmp_path):
    import dbx
    assert "project_tags" in dbx.TABLES and dbx.TABLES.index("project_tags") > dbx.TABLES.index("projects")
    seed(amy); amy.post("/projects/1/tags", {"tags": "gifts"})
    import dump; c = env.db(); assert dump.dump(c)["tables"]["project_tags"]["rows"] == [[1, "gifts"]]

@pytest.mark.skipif(not TEST_DB, reason="needs a server database")
def test_server_database_without_the_new_columns_is_upgraded(make_env):
    env = make_env(); import migrations, dbx
    c = dbx.connect(dbx.CFG); cur = c.raw.cursor()
    cur.execute("ALTER TABLE projects DROP COLUMN created"); cur.execute("ALTER TABLE projects DROP COLUMN updated")
    cur.execute("INSERT INTO users(username,pw,is_admin) VALUES('amy','x',1)"); cur.execute("INSERT INTO projects(user_id,name) VALUES(1,'Hat')")
    cur.execute("UPDATE schema_version SET version=%s", (len(migrations.STEPS) - 1,)); c.commit(); c.close()
    c = dbx.connect(dbx.CFG); migrations.run_server(c); c.close()
    c = dbx.connect(dbx.CFG); created, updated = c.execute("SELECT created, updated FROM projects").fetchone(); c.close()
    assert created and created == updated
