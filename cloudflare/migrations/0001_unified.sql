CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY NOT NULL, username TEXT UNIQUE NOT NULL COLLATE NOCASE,
  password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK (role IN ('ADMIN','USER')),
  is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY NOT NULL, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash TEXT NOT NULL UNIQUE, expires_at INTEGER NOT NULL, revoked_at INTEGER
);
CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS sessions_expiration ON sessions(expires_at);

CREATE TABLE IF NOT EXISTS login_attempts (
  key TEXT PRIMARY KEY NOT NULL, step INTEGER NOT NULL, hits INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS login_step ON login_attempts(step);

CREATE TABLE IF NOT EXISTS videos (
  id TEXT PRIMARY KEY NOT NULL, title TEXT NOT NULL, original_filename TEXT NOT NULL,
  original_size INTEGER, processed_size INTEGER, duration REAL,
  resolution TEXT, frame_rate TEXT, video_codec TEXT NOT NULL, audio_codec TEXT,
  segment_count INTEGER, playlist_key TEXT NOT NULL, r2_prefix TEXT NOT NULL,
  codecs TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('READY','DELETING','DELETED')),
  created_at TEXT NOT NULL, playback_checked_at TEXT, archived_source_size INTEGER
);
CREATE INDEX IF NOT EXISTS videos_visible ON videos(status, created_at DESC, id DESC);

