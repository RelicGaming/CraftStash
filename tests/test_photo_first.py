"""Photo-first adding: the photo comes first on the item form and the page may preview it before upload."""
import io
from conftest import seed, png_bytes

def test_new_item_form_starts_with_the_photo(amy):
    seed(amy); page = amy.html("/types/1/items/new")
    assert "data-photo-first" in page and 'name="photo" accept="image/*"' in page
    assert page.index('name="photo"') < page.index('name="name"')         # photo before name
    assert page.count('name="photo"') == 1 and "Start with a photo" in page
    assert "data-colour-from-photo" not in page and "Remove photo" not in page

def test_preview_of_a_chosen_photo_is_allowed_by_csp(amy):
    csp = amy.get("/types/1/items/new").headers["Content-Security-Policy"]
    assert "img-src 'self' data: blob:;" in csp and "script-src 'self';" in csp

def test_photo_and_swatch_save_together(env, amy):
    seed(amy)
    amy.post("/types/1/items/new", {"name": "Teal", "qty": "1", "has_hex": "1", "hex": "#2a8c8c", "colour": "Teal",
                                    "photo": (io.BytesIO(png_bytes()), "yarn.png")}, content_type="multipart/form-data")
    page = amy.html("/collections/1")
    assert "--c:#2a8c8c" in page and "/photo" in page
    edit = amy.html("/items/3/edit")
    assert "data-colour-from-photo" in edit and "Remove photo" in edit and "Choose a new photo" in edit
    assert 'class="photo-preview" alt="" src="/photos/3"' in edit

def test_edit_without_photo_hides_the_preview(amy):
    seed(amy); page = amy.html("/items/1/edit")
    assert 'class="photo-preview" alt="" hidden' in page and "data-colour-from-photo" not in page
