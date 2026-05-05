from __future__ import annotations

import sqlite3
from pathlib import Path


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS items (
  id TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  native_id TEXT NOT NULL,
  event_ts TEXT NOT NULL,
  fetched_ts TEXT NOT NULL,
  author TEXT,
  title TEXT,
  body TEXT,
  body_zh TEXT,
  url TEXT,
  category TEXT,
  relevance INTEGER,
  has_curated INTEGER DEFAULT 0,
  has_bulk INTEGER DEFAULT 0,
  preferred_src TEXT NOT NULL,
  first_seen_ts TEXT NOT NULL,
  last_seen_ts TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_items_ts ON items(event_ts DESC);
CREATE INDEX IF NOT EXISTS idx_items_src_ts ON items(source, event_ts DESC);
CREATE INDEX IF NOT EXISTS idx_items_source_native_id ON items(source, native_id);
CREATE INDEX IF NOT EXISTS idx_items_author ON items(author);
CREATE INDEX IF NOT EXISTS idx_items_category ON items(category) WHERE category IS NOT NULL;

CREATE TABLE IF NOT EXISTS item_raw_refs (
  item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  provenance TEXT NOT NULL,
  raw_path TEXT NOT NULL,
  raw_locator TEXT,
  raw_hash TEXT,
  raw_mtime TEXT,
  first_seen_ts TEXT NOT NULL,
  last_seen_ts TEXT NOT NULL,
  PRIMARY KEY (item_id, provenance)
);

CREATE INDEX IF NOT EXISTS idx_raw_refs_path ON item_raw_refs(raw_path);

CREATE TABLE IF NOT EXISTS ingest_runs (
  run_id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_ts TEXT NOT NULL,
  finished_ts TEXT,
  mode TEXT NOT NULL,
  sources TEXT,
  files_scanned INTEGER DEFAULT 0,
  files_skipped INTEGER DEFAULT 0,
  files_ok INTEGER DEFAULT 0,
  files_failed INTEGER DEFAULT 0,
  rows_inserted INTEGER DEFAULT 0,
  rows_updated INTEGER DEFAULT 0,
  rows_refs_added INTEGER DEFAULT 0,
  ok INTEGER
);

CREATE TABLE IF NOT EXISTS ingest_files (
  path TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  last_run_id INTEGER REFERENCES ingest_runs(run_id),
  last_mtime TEXT NOT NULL,
  last_size INTEGER NOT NULL,
  rows_seen INTEGER DEFAULT 0,
  status TEXT NOT NULL,
  error TEXT
);

CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
  title, body, body_zh, author,
  content='items',
  content_rowid='rowid',
  tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS items_ai AFTER INSERT ON items BEGIN
  INSERT INTO items_fts(rowid, title, body, body_zh, author)
  VALUES (new.rowid, new.title, new.body, new.body_zh, new.author);
END;

CREATE TRIGGER IF NOT EXISTS items_ad AFTER DELETE ON items BEGIN
  INSERT INTO items_fts(items_fts, rowid, title, body, body_zh, author)
  VALUES('delete', old.rowid, old.title, old.body, old.body_zh, old.author);
END;

CREATE TRIGGER IF NOT EXISTS items_au AFTER UPDATE ON items BEGIN
  INSERT INTO items_fts(items_fts, rowid, title, body, body_zh, author)
  VALUES('delete', old.rowid, old.title, old.body, old.body_zh, old.author);
  INSERT INTO items_fts(rowid, title, body, body_zh, author)
  VALUES (new.rowid, new.title, new.body, new.body_zh, new.author);
END;
"""

DROP_ALL_SQL = """
DROP TRIGGER IF EXISTS items_ai;
DROP TRIGGER IF EXISTS items_ad;
DROP TRIGGER IF EXISTS items_au;
DROP TABLE IF EXISTS items_fts;
DROP TABLE IF EXISTS item_raw_refs;
DROP TABLE IF EXISTS ingest_files;
DROP TABLE IF EXISTS ingest_runs;
DROP TABLE IF EXISTS meta;
DROP TABLE IF EXISTS items;
"""


def bootstrap_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(SCHEMA_SQL)


def rebuild_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.executescript(DROP_ALL_SQL)
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(SCHEMA_SQL)
