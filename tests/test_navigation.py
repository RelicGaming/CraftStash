"""Menus, page names and back links."""
import re
from conftest import seed

def tabs(html): return re.findall(r'<span>(Collections|Projects|Shopping|Search)</span>', html)
def current_tab(html):
    nav = html[html.index('<nav class="tabs"'):html.index('</nav>')]
    return re.findall(r'aria-current="page"><svg class="i"><use href="#i-\w+"/></svg><span>(\w+)</span>', nav)

def test_four_main_tabs_and_an_account_menu(amy, bob):
    html = amy.html("/")
    assert tabs(html) == ["Collections", "Projects", "Shopping", "Search"]
    assert 'aria-label="Account menu"' in html and "Account and API tokens" in html and "Appearance" in html and "Log out" in html
    assert ">Admin</a>" in html and ">Users</a>" not in html          # admin moved out of the main tabs
    assert ">Admin</a>" not in bob.html("/")                          # and only admins see it

def test_the_right_tab_is_highlighted_on_every_page(amy):
    seed(amy)
    for url, tab in [("/", "Collections"), ("/collections/1", "Collections"), ("/collections/1/activity", "Collections"),
                     ("/collections/1/import", "Collections"), ("/types/1/fields", "Collections"), ("/items/1/edit", "Collections"),
                     ("/projects", "Projects"), ("/projects/1", "Projects"), ("/shopping", "Shopping"), ("/search?q=x", "Search")]:
        assert current_tab(amy.html(url)) == [tab], url
    for url in ("/account", "/appearance", "/admin"):
        html = amy.html(url); assert current_tab(html) == [] and 'class="who current"' in html, url

def test_back_links_name_where_they_go(amy):
    seed(amy)
    for url in ("/items/1/edit", "/types/1/items/new", "/types/1/fields"):
        assert re.search(r'class="back" href="/collections/1"><svg[^>]*><use[^>]*/></svg>Knitting</a>', amy.html(url)), url
    assert "Back to collection" not in amy.html("/items/1/edit")

def test_type_page_is_named_for_editing(amy):
    seed(amy); html = amy.html("/types/1/fields")
    assert "<title>Edit Yarn - Craft Stash</title>" in html and "Name and unit" in html and "a material type in Knitting" in html
    assert ">Edit type</a>" in amy.html("/collections/1")

def test_admin_page_is_called_admin(amy):
    html = amy.html("/admin")
    assert "<title>Admin - Craft Stash</title>" in html and "<h1>Admin</h1>" in html and ">Users</h2>" in html

def test_collection_actions_sit_in_a_more_menu(amy, bob):
    seed(amy); html = amy.html("/collections/1")
    menu = html[html.index('aria-label="More collection actions"'):]
    menu = menu[:menu.index("</details>")]
    assert "Import from CSV" in menu and "Export to CSV" in menu and 'href="#settings"' in menu and "Leave" not in menu
    assert 'id="settings"' in html
    amy.post("/collections/1/share", {"username": "bob", "role": "view"})
    other = bob.html("/collections/1")
    assert "Leave collection" in other and "Import from CSV" not in other and 'href="#settings"' not in other

def test_project_keeps_one_main_action_and_the_rest_in_more(amy):
    seed(amy); amy.post("/projects/1/assign", {"item_id": "1", "qty": "1"})
    html = amy.html("/projects/1")
    a = html.index('<div class="actions">'); head = html[a:html.index("</details>", a)]
    assert "Mark complete" in head and head.index("Mark complete") < head.index("More")
    menu = head[head.index("More project actions"):]
    assert all(x in menu for x in ("Print", "Duplicate", "Save as template", "Complete without using stock"))
    amy.post("/projects/1/complete", {"stock": "keep"})
    done = amy.html("/projects/1"); a = done.index('<div class="actions">'); head = done[a:done.index("</details>", a)]
    assert ">Reopen</button>" in head and "Mark complete" not in head and "Complete without using stock" not in head
