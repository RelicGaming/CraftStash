-- Craft Stash on MariaDB 10.6+ / MySQL 8.0.13+. The same tables as sqlite.sql: timestamps are 'YYYY-MM-DD HH:MM:SS'
-- UTC text, JSON columns are text, quantities are DOUBLE. Text compares case-sensitively (utf8mb4_bin) like SQLite,
-- except usernames and storage places (utf8mb4_unicode_ci, like SQLite's COLLATE NOCASE). Indexed text columns are
-- VARCHARs short enough for an index key. Runs as one script on every start, so everything is idempotent.

CREATE TABLE IF NOT EXISTS schema_version(version INTEGER NOT NULL) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS users(id BIGINT AUTO_INCREMENT PRIMARY KEY, username VARCHAR(191) COLLATE utf8mb4_unicode_ci NOT NULL UNIQUE, pw TEXT NOT NULL, is_admin INTEGER NOT NULL DEFAULT 0, theme TEXT NOT NULL DEFAULT (''), active INTEGER NOT NULL DEFAULT 1)
  ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
CREATE TABLE IF NOT EXISTS pools(id BIGINT AUTO_INCREMENT PRIMARY KEY, user_id BIGINT NOT NULL, name TEXT NOT NULL, craft TEXT NOT NULL DEFAULT (''),
  INDEX ix_pools_user (user_id), FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
CREATE TABLE IF NOT EXISTS types(id BIGINT AUTO_INCREMENT PRIMARY KEY, pool_id BIGINT NOT NULL, name TEXT NOT NULL, unit TEXT NOT NULL DEFAULT ('pcs'), fields MEDIUMTEXT NOT NULL DEFAULT ('[]'),
  INDEX ix_types_pool (pool_id), FOREIGN KEY (pool_id) REFERENCES pools(id) ON DELETE CASCADE) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
CREATE TABLE IF NOT EXISTS items(id BIGINT AUTO_INCREMENT PRIMARY KEY, type_id BIGINT NOT NULL, name TEXT NOT NULL, colour TEXT NOT NULL DEFAULT (''), hex TEXT NOT NULL DEFAULT (''), subtype TEXT NOT NULL DEFAULT (''), qty DOUBLE NOT NULL DEFAULT 0, price DOUBLE, low_at DOUBLE, location TEXT NOT NULL DEFAULT (''), notes TEXT NOT NULL DEFAULT (''), extra MEDIUMTEXT NOT NULL DEFAULT ('{}'), photo TEXT NOT NULL DEFAULT (''),
  INDEX ix_items_type (type_id), FOREIGN KEY (type_id) REFERENCES types(id) ON DELETE CASCADE) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
CREATE TABLE IF NOT EXISTS projects(id BIGINT AUTO_INCREMENT PRIMARY KEY, user_id BIGINT NOT NULL, name TEXT NOT NULL, status VARCHAR(20) NOT NULL DEFAULT 'planned', notes TEXT NOT NULL DEFAULT (''), created VARCHAR(19) NOT NULL DEFAULT '', updated VARCHAR(19) NOT NULL DEFAULT '', photo TEXT NOT NULL DEFAULT (''), source TEXT NOT NULL DEFAULT (''),
  INDEX ix_projects_user (user_id), FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
CREATE TABLE IF NOT EXISTS assignments(id BIGINT AUTO_INCREMENT PRIMARY KEY, project_id BIGINT NOT NULL, item_id BIGINT NOT NULL, qty DOUBLE NOT NULL, consumed INTEGER NOT NULL DEFAULT 0, used DOUBLE NOT NULL DEFAULT 0,
  UNIQUE KEY uq_assign (project_id, item_id), INDEX ix_assign_item (item_id),
  FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE, FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
CREATE TABLE IF NOT EXISTS files(id BIGINT AUTO_INCREMENT PRIMARY KEY, project_id BIGINT NOT NULL, name TEXT NOT NULL, `stored` TEXT NOT NULL, size BIGINT NOT NULL,
  INDEX ix_files_project (project_id), FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
CREATE TABLE IF NOT EXISTS pool_shares(pool_id BIGINT NOT NULL, user_id BIGINT NOT NULL, can_edit INTEGER NOT NULL DEFAULT 0,
  UNIQUE KEY uq_pool_share (pool_id, user_id), INDEX ix_pool_shares_user (user_id),
  FOREIGN KEY (pool_id) REFERENCES pools(id) ON DELETE CASCADE, FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
CREATE TABLE IF NOT EXISTS project_shares(project_id BIGINT NOT NULL, user_id BIGINT NOT NULL,
  UNIQUE KEY uq_project_share (project_id, user_id), INDEX ix_project_shares_user (user_id),
  FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE, FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
CREATE TABLE IF NOT EXISTS project_tags(project_id BIGINT NOT NULL, tag VARCHAR(40) NOT NULL,
  UNIQUE KEY uq_project_tag (project_id, tag), INDEX ix_project_tags_tag (tag),
  FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
CREATE TABLE IF NOT EXISTS steps(id BIGINT AUTO_INCREMENT PRIMARY KEY, project_id BIGINT NOT NULL, pos INTEGER NOT NULL, title TEXT NOT NULL DEFAULT (''), body TEXT NOT NULL DEFAULT (''),
  INDEX ix_steps_project (project_id), FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
CREATE TABLE IF NOT EXISTS settings(`key` VARCHAR(191) PRIMARY KEY, value TEXT NOT NULL) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
CREATE TABLE IF NOT EXISTS activity(id BIGINT AUTO_INCREMENT PRIMARY KEY, ts VARCHAR(19) NOT NULL DEFAULT (DATE_FORMAT(UTC_TIMESTAMP(), '%Y-%m-%d %H:%i:%s')), user_id BIGINT NULL, pool_id BIGINT NOT NULL, item_id BIGINT, kind VARCHAR(40) NOT NULL, text TEXT NOT NULL, delta DOUBLE, undone INTEGER NOT NULL DEFAULT 0,
  INDEX ix_activity_pool (pool_id, id), INDEX ix_activity_user (user_id), INDEX ix_activity_ts (ts),
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL, FOREIGN KEY (pool_id) REFERENCES pools(id) ON DELETE CASCADE) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
CREATE TABLE IF NOT EXISTS step_photos(id BIGINT AUTO_INCREMENT PRIMARY KEY, step_id BIGINT NOT NULL, `stored` TEXT NOT NULL, size BIGINT NOT NULL,
  INDEX ix_step_photos_step (step_id), FOREIGN KEY (step_id) REFERENCES steps(id) ON DELETE CASCADE) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
CREATE TABLE IF NOT EXISTS item_places(id BIGINT AUTO_INCREMENT PRIMARY KEY, item_id BIGINT NOT NULL, place VARCHAR(191) COLLATE utf8mb4_unicode_ci NOT NULL, qty DOUBLE NOT NULL,
  UNIQUE KEY uq_item_place (item_id, place), FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
CREATE TABLE IF NOT EXISTS api_tokens(id BIGINT AUTO_INCREMENT PRIMARY KEY, user_id BIGINT NOT NULL, name TEXT NOT NULL, hash VARCHAR(128) NOT NULL UNIQUE, can_write INTEGER NOT NULL DEFAULT 0, created VARCHAR(19) NOT NULL DEFAULT (DATE_FORMAT(UTC_TIMESTAMP(), '%Y-%m-%d %H:%i:%s')), last_used VARCHAR(19),
  INDEX ix_api_tokens_user (user_id), FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
CREATE TABLE IF NOT EXISTS user_identities(id BIGINT AUTO_INCREMENT PRIMARY KEY, user_id BIGINT NOT NULL, issuer VARCHAR(255) NOT NULL, subject VARCHAR(255) NOT NULL, label TEXT NOT NULL DEFAULT (''), created VARCHAR(19) NOT NULL DEFAULT (DATE_FORMAT(UTC_TIMESTAMP(), '%Y-%m-%d %H:%i:%s')),
  UNIQUE KEY uq_identity (issuer, subject), INDEX ix_user_identities_user (user_id), FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
CREATE TABLE IF NOT EXISTS wishes(id BIGINT AUTO_INCREMENT PRIMARY KEY, user_id BIGINT NOT NULL, name TEXT NOT NULL, colour TEXT NOT NULL DEFAULT (''), hex TEXT NOT NULL DEFAULT (''), qty DOUBLE NOT NULL DEFAULT 1, unit TEXT NOT NULL DEFAULT (''), notes TEXT NOT NULL DEFAULT (''), photo TEXT NOT NULL DEFAULT (''), type_id BIGINT NULL, item_id BIGINT NULL, project_id BIGINT NULL, got INTEGER NOT NULL DEFAULT 0, created VARCHAR(19) NOT NULL DEFAULT '', got_at VARCHAR(19) NOT NULL DEFAULT '',
  INDEX ix_wishes_user (user_id), INDEX ix_wishes_type (type_id), INDEX ix_wishes_item (item_id), INDEX ix_wishes_project (project_id),
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE, FOREIGN KEY (type_id) REFERENCES types(id) ON DELETE SET NULL,
  FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE SET NULL, FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
CREATE TABLE IF NOT EXISTS wish_sightings(id BIGINT AUTO_INCREMENT PRIMARY KEY, wish_id BIGINT NOT NULL, shop TEXT NOT NULL DEFAULT (''), url TEXT NOT NULL DEFAULT (''), price DOUBLE, seen VARCHAR(10) NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT (''),
  INDEX ix_wish_sightings_wish (wish_id), FOREIGN KEY (wish_id) REFERENCES wishes(id) ON DELETE CASCADE) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE OR REPLACE VIEW access AS
  SELECT id AS pool_id, user_id, 1 AS can_edit, 1 AS is_owner FROM pools
  UNION ALL SELECT pool_id, user_id, can_edit, 0 FROM pool_shares;
CREATE OR REPLACE VIEW stock AS
  SELECT i.*, t.name AS tname, t.unit, t.pool_id, p.name AS pname, p.user_id,
         i.qty - COALESCE((SELECT SUM(a.qty) FROM assignments a WHERE a.item_id = i.id AND a.consumed = 0
                     AND a.project_id NOT IN (SELECT id FROM projects WHERE status IN ('template', 'idea'))), 0) AS free
  FROM items i JOIN types t ON t.id = i.type_id JOIN pools p ON p.id = t.pool_id;
