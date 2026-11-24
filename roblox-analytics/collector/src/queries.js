import { db } from './db.js';
import { config } from './config.js';

const now = () => Math.floor(Date.now() / 1000);
const liveCutoff = () => now() - config.staleSeconds;

export function liveServers() {
  return db.prepare(`
    SELECT * FROM servers
    WHERE closed_at IS NULL AND lost = 0 AND last_seen >= ?
    ORDER BY players DESC, job_id`).all(liveCutoff());
}

export function findServer(jobIdOrPrefix) {
  return db.prepare('SELECT * FROM servers WHERE job_id LIKE ? ORDER BY last_seen DESC LIMIT 1').get(`${jobIdOrPrefix}%`);
}

export function latestSample(jobId) {
  const row = db.prepare('SELECT json FROM samples WHERE job_id = ? ORDER BY t DESC LIMIT 1').get(jobId);
  return row ? JSON.parse(row.json) : null;
}

export function serverHistory(jobId, minutes = 30) {
  return db.prepare('SELECT t, players, fps, heartbeat_ms, memory_mb, ping_avg FROM samples WHERE job_id = ? AND t >= ? ORDER BY t')
    .all(jobId, now() - minutes * 60);
}

const avg = (list, key) => {
  const values = list.map((x) => x[key]).filter((v) => v != null);
  return values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;
};

export function overview() {
  const servers = liveServers();
  const health = { healthy: 0, degraded: 0, critical: 0 };
  for (const s of servers) health[s.health ?? 'healthy'] = (health[s.health ?? 'healthy'] ?? 0) + 1;

  const startOfDay = new Date();
  startOfDay.setUTCHours(0, 0, 0, 0);
  const peakToday = db.prepare(`
    SELECT MAX(total) AS peak FROM (
      SELECT (t / 60) AS minute, SUM(players) AS total FROM (
        SELECT job_id, t, MAX(players) AS players FROM samples WHERE t >= ? GROUP BY job_id, t / 60
      ) GROUP BY minute)`).get(Math.floor(startOfDay.getTime() / 1000))?.peak ?? 0;

  const versions = [...new Set(servers.map((s) => s.place_version))].sort((a, b) => b - a);

  return {
    servers: servers.length,
    players: servers.reduce((n, s) => n + (s.players ?? 0), 0),
    avgServerFps: avg(servers, 'fps'),
    avgPingMs: avg(servers, 'ping_avg'),
    avgClientFps: avg(servers, 'client_fps'),
    avgMemoryMb: avg(servers, 'memory_mb'),
    peakToday,
    versions,
    health,
    capturesToday: db.prepare('SELECT COUNT(*) AS n FROM captures WHERE started_at >= ?').get(Math.floor(startOfDay.getTime() / 1000)).n,
  };
}

/** Total concurrent players in buckets over the last `hours`, for a sparkline. */
export function playerTrend(hours = 24, buckets = 24) {
  const from = now() - hours * 3600;
  const size = (hours * 3600) / buckets;
  const rows = db.prepare(`
    SELECT CAST((minute * 60 - ?) / ? AS INTEGER) AS bucket, MAX(total) AS players FROM (
      SELECT t / 60 AS minute, SUM(players) AS total FROM (
        SELECT job_id, t, MAX(players) AS players FROM samples WHERE t >= ? GROUP BY job_id, t / 60
      ) GROUP BY minute
    ) GROUP BY bucket ORDER BY bucket`).all(from, size, from);
  const out = new Array(buckets).fill(0);
  for (const r of rows) if (r.bucket >= 0 && r.bucket < buckets) out[r.bucket] = r.players;
  return out;
}

export function recentErrors(minutes = 60, limit = 8) {
  return db.prepare(`
    SELECT origin, message, MAX(source) AS source, SUM(count) AS total, COUNT(DISTINCT job_id) AS servers
    FROM errors WHERE t >= ? GROUP BY origin, message ORDER BY total DESC LIMIT ?`).all(now() - minutes * 60, limit);
}

export function platformMix(minutes = 10) {
  const rows = db.prepare('SELECT job_id, json FROM samples WHERE t >= ? ORDER BY t').all(now() - minutes * 60);
  const latestByJob = new Map(); // later samples overwrite earlier ones
  for (const r of rows) latestByJob.set(r.job_id, JSON.parse(r.json).players?.platforms ?? {});
  const mix = {};
  for (const platforms of latestByJob.values()) {
    for (const [k, v] of Object.entries(platforms)) mix[k] = (mix[k] ?? 0) + v;
  }
  return mix;
}

const BARS = '▁▂▃▄▅▆▇█';
export function sparkline(values) {
  const max = Math.max(...values, 1);
  return values.map((v) => BARS[Math.min(BARS.length - 1, Math.floor((v / max) * (BARS.length - 1)))]).join('');
}
