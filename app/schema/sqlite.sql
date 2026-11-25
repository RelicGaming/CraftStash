CREATE TABLE IF NOT EXISTS schema_version(version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL COLLATE NOCASE, pw TEXT NOT NULL, is_admin INTEGER NOT NULL DEFAULT 0, theme TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS pools(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users ON DELETE CASCADE, name TEXT NOT NULL, craft TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS types(id INTEGER PRIMARY KEY, pool_id INTEGER NOT NULL REFERENCES pools ON DELETE CASCADE, name TEXT NOT NULL, unit TEXT NOT NULL DEFAULT 'pcs', fields TEXT NOT NULL DEFAULT '[]');
CREATE TABLE IF NOT EXISTS items(id INTEGER PRIMARY KEY, type_id INTEGER NOT NULL REFERENCES types ON DELETE CASCADE, name TEXT NOT NULL, colour TEXT NOT NULL DEFAULT '', hex TEXT NOT NULL DEFAULT '', subtype TEXT NOT NULL DEFAULT '', qty REAL NOT NULL DEFAULT 0, price REAL, low_at REAL, location TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '', extra TEXT NOT NULL DEFAULT '{}', photo TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS projects(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users ON DELETE CASCADE, name TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'planned', notes TEXT NOT NULL DEFAULT '', created TEXT NOT NULL DEFAULT '', updated TEXT NOT NULL DEFAULT '', photo TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS assignments(id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects ON DELETE CASCADE, item_id INTEGER NOT NULL REFERENCES items ON DELETE CASCADE, qty REAL NOT NULL, consumed INTEGER NOT NULL DEFAULT 0, used REAL NOT NULL DEFAULT 0, UNIQUE(project_id, item_id));
CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects ON DELETE CASCADE, name TEXT NOT NULL, stored TEXT NOT NULL, size INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS pool_shares(pool_id INTEGER NOT NULL REFERENCES pools ON DELETE CASCADE, user_id INTEGER NOT NULL REFERENCES users ON DELETE CASCADE, can_edit INTEGER NOT NULL DEFAULT 0, UNIQUE(pool_id, user_id));
CREATE TABLE IF NOT EXISTS project_shares(project_id INTEGER NOT NULL REFERENCES projects ON DELETE CASCADE, user_id INTEGER NOT NULL REFERENCES users ON DELETE CASCADE, UNIQUE(project_id, user_id));
CREATE TABLE IF NOT EXISTS steps(id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects ON DELETE CASCADE, pos INTEGER NOT NULL, title TEXT NOT NULL DEFAULT '', body TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS activity(id INTEGER PRIMARY KEY, ts TEXT NOT NULL DEFAULT (datetime('now')), user_id INTEGER REFERENCES users ON DELETE SET NULL, pool_id INTEGER NOT NULL REFERENCES pools ON DELETE CASCADE, item_id INTEGER, kind TEXT NOT NULL, text TEXT NOT NULL, delta REAL, undone INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS ix_types_pool ON types(pool_id);
CREATE INDEX IF NOT EXISTS ix_items_type ON items(type_id);
CREATE INDEX IF NOT EXISTS ix_assign_project ON assignments(project_id);
CREATE INDEX IF NOT EXISTS ix_assign_item ON assignments(item_id);
CREATE INDEX IF NOT EXISTS ix_files_project ON files(project_id);
CREATE INDEX IF NOT EXISTS ix_steps_project ON steps(project_id);
CREATE TABLE IF NOT EXISTS step_photos(id INTEGER PRIMARY KEY, step_id INTEGER NOT NULL REFERENCES steps ON DELETE CASCADE, stored TEXT NOT NULL, size INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS ix_step_photos_step ON step_photos(step_id);
CREATE INDEX IF NOT EXISTS ix_activity_pool ON activity(pool_id, id);
CREATE INDEX IF NOT EXISTS ix_pool_shares_user ON pool_shares(user_id);
CREATE INDEX IF NOT EXISTS ix_project_shares_user ON project_shares(user_id);
CREATE TABLE IF NOT EXISTS project_tags(project_id INTEGER NOT NULL REFERENCES projects ON DELETE CASCADE, tag TEXT NOT NULL, UNIQUE(project_id, tag));
CREATE INDEX IF NOT EXISTS ix_project_tags_tag ON project_tags(tag);
CREATE INDEX IF NOT EXISTS ix_pools_user ON pools(user_id);
CREATE INDEX IF NOT EXISTS ix_projects_user ON projects(user_id);
CREATE INDEX IF NOT EXISTS ix_activity_user ON activity(user_id);
CREATE INDEX IF NOT EXISTS ix_activity_ts ON activity(ts);
CREATE TABLE IF NOT EXISTS item_places(id INTEGER PRIMARY KEY, item_id INTEGER NOT NULL REFERENCES items ON DELETE CASCADE, place TEXT NOT NULL COLLATE NOCASE, qty REAL NOT NULL, UNIQUE(item_id, place));
-- Part of an item's stock kept somewhere other than its main location. The places never add up to more than items.qty:
-- the rest is at the main location, and when the total drops below what the places hold they shrink in proportion.
CREATE TRIGGER IF NOT EXISTS places_fit AFTER UPDATE OF qty ON items
WHEN NEW.qty < (SELECT COALESCE(SUM(qty), 0) FROM item_places WHERE item_id = NEW.id)
BEGIN
  UPDATE item_places SET qty = qty * NEW.qty / (SELECT SUM(qty) FROM item_places WHERE item_id = NEW.id) WHERE item_id = NEW.id;
  DELETE FROM item_places WHERE item_id = NEW.id AND qty < 1e-9;
END;
CREATE VIEW IF NOT EXISTS access AS
  SELECT id AS pool_id, user_id, 1 AS can_edit, 1 AS is_owner FROM pools
  UNION ALL SELECT pool_id, user_id, can_edit, 0 FROM pool_shares;
CREATE VIEW IF NOT EXISTS stock AS
  SELECT i.*, t.name AS tname, t.unit, t.pool_id, p.name AS pname, p.user_id,
         i.qty - COALESCE((SELECT SUM(a.qty) FROM assignments a WHERE a.item_id = i.id AND a.consumed = 0
                     AND a.project_id NOT IN (SELECT id FROM projects WHERE status IN ('template', 'idea'))), 0) AS free
  FROM items i JOIN types t ON t.id = i.type_id JOIN pools p ON p.id = t.pool_id;
CREATE TABLE IF NOT EXISTS api_tokens(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users ON DELETE CASCADE, name TEXT NOT NULL, hash TEXT UNIQUE NOT NULL, can_write INTEGER NOT NULL DEFAULT 0, created TEXT NOT NULL DEFAULT (datetime('now')), last_used TEXT);
CREATE INDEX IF NOT EXISTS ix_api_tokens_user ON api_tokens(user_id);
-- Single sign-on: which provider account (issuer + subject) logs in as which user.
CREATE TABLE IF NOT EXISTS user_identities(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users ON DELETE CASCADE, issuer TEXT NOT NULL, subject TEXT NOT NULL, label TEXT NOT NULL DEFAULT '', created TEXT NOT NULL DEFAULT (datetime('now')), UNIQUE(issuer, subject));
CREATE INDEX IF NOT EXISTS ix_user_identities_user ON user_identities(user_id);
-- Wishlist: things someone wants but doesn't own yet (or an item they're watching the price of), and where they saw them.
-- A wish linked to a type is visible to everyone who can see that type's collection; otherwise only to its owner.
CREATE TABLE IF NOT EXISTS wishes(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users ON DELETE CASCADE, name TEXT NOT NULL, colour TEXT NOT NULL DEFAULT '', hex TEXT NOT NULL DEFAULT '', qty REAL NOT NULL DEFAULT 1, unit TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '', photo TEXT NOT NULL DEFAULT '', type_id INTEGER REFERENCES types ON DELETE SET NULL, item_id INTEGER REFERENCES items ON DELETE SET NULL, project_id INTEGER REFERENCES projects ON DELETE SET NULL, got INTEGER NOT NULL DEFAULT 0, created TEXT NOT NULL DEFAULT '', got_at TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS ix_wishes_user ON wishes(user_id);
CREATE INDEX IF NOT EXISTS ix_wishes_type ON wishes(type_id);
CREATE INDEX IF NOT EXISTS ix_wishes_item ON wishes(item_id);
CREATE INDEX IF NOT EXISTS ix_wishes_project ON wishes(project_id);
CREATE TABLE IF NOT EXISTS wish_sightings(id INTEGER PRIMARY KEY, wish_id INTEGER NOT NULL REFERENCES wishes ON DELETE CASCADE, shop TEXT NOT NULL DEFAULT '', url TEXT NOT NULL DEFAULT '', price REAL, seen TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS ix_wish_sightings_wish ON wish_sightings(wish_id);
