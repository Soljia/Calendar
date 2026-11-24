import { DatabaseSync } from 'node:sqlite';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { config } from './config.js';

mkdirSync(config.dataDir, { recursive: true });

export const db = new DatabaseSync(join(config.dataDir, 'analytics.db'));

db.exec(`
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS servers (
  job_id TEXT PRIMARY KEY,
  universe_id INTEGER, place_id INTEGER, place_version INTEGER,
  private_server_id TEXT, reserved INTEGER,
  started_at INTEGER, last_seen INTEGER, closed_at INTEGER, lost INTEGER DEFAULT 0,
  ip TEXT,
  players INTEGER, max_players INTEGER, fps REAL, heartbeat_ms REAL, memory_mb REAL,
  ping_avg REAL, client_fps REAL, health TEXT, uptime INTEGER
);

CREATE TABLE IF NOT EXISTS samples (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT NOT NULL, t INTEGER NOT NULL,
  players INTEGER, fps REAL, fps_min REAL, frame_p99_ms REAL, frame_spikes INTEGER,
  heartbeat_ms REAL, physics_ms REAL, memory_mb REAL, lua_heap_mb REAL,
  recv_kbps REAL, send_kbps REAL, instances INTEGER,
  ping_avg REAL, ping_p95 REAL, client_fps REAL, low_fps_players INTEGER,
  errors INTEGER, warnings INTEGER, health TEXT,
  json TEXT
);
CREATE INDEX IF NOT EXISTS samples_job_t ON samples(job_id, t);
CREATE INDEX IF NOT EXISTS samples_t ON samples(t);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT, t INTEGER, name TEXT, json TEXT
);
CREATE INDEX IF NOT EXISTS events_t ON events(t);

CREATE TABLE IF NOT EXISTS errors (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id TEXT, t INTEGER, origin TEXT, message TEXT, source TEXT, count INTEGER
);
CREATE INDEX IF NOT EXISTS errors_t ON errors(t);

CREATE TABLE IF NOT EXISTS captures (
  id TEXT PRIMARY KEY,
  job_id TEXT, requested_by TEXT, duration INTEGER,
  started_at INTEGER, finished_at INTEGER, status TEXT,
  chunks INTEGER DEFAULT 0, snapshot_json TEXT, summary_json TEXT
);

CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
`);

export const kv = {
  get: (k) => db.prepare('SELECT v FROM kv WHERE k = ?').get(k)?.v,
  set: (k, v) => db.prepare('INSERT INTO kv (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v').run(k, String(v)),
};

export function pruneOldData() {
  const cutoff = Math.floor(Date.now() / 1000) - config.retentionDays * 86400;
  db.prepare('DELETE FROM samples WHERE t < ?').run(cutoff);
  db.prepare('DELETE FROM events WHERE t < ?').run(cutoff);
  db.prepare('DELETE FROM errors WHERE t < ?').run(cutoff);
  db.prepare('DELETE FROM servers WHERE last_seen < ?').run(cutoff);
}
