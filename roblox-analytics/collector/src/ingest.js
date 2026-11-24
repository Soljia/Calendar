import { EventEmitter } from 'node:events';
import { db } from './db.js';
import { classify } from './health.js';
import { config } from './config.js';

/** Emits: 'sample' (jobId, sample, health), 'lifecycle' (jobId, event), 'event' (jobId, name, fields), 'alert' (alert) */
export const bus = new EventEmitter();

const now = () => Math.floor(Date.now() / 1000);
const str = (v, max = 200) => (typeof v === 'string' ? v.slice(0, max) : null);
const numOrNull = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : null);

const upsertServer = db.prepare(`
INSERT INTO servers (job_id, universe_id, place_id, place_version, private_server_id, reserved, started_at, last_seen, ip)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(job_id) DO UPDATE SET last_seen = excluded.last_seen, place_version = excluded.place_version, ip = excluded.ip, lost = 0
`);
const updateServerLive = db.prepare(`
UPDATE servers SET players = ?, max_players = ?, fps = ?, heartbeat_ms = ?, memory_mb = ?, ping_avg = ?, client_fps = ?, health = ?, uptime = ?
WHERE job_id = ?
`);
const insertSample = db.prepare(`
INSERT INTO samples (job_id, t, players, fps, fps_min, frame_p99_ms, frame_spikes, heartbeat_ms, physics_ms, memory_mb, lua_heap_mb,
  recv_kbps, send_kbps, instances, ping_avg, ping_p95, client_fps, low_fps_players, errors, warnings, health, json)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
`);
const insertError = db.prepare('INSERT INTO errors (job_id, t, origin, message, source, count) VALUES (?, ?, ?, ?, ?, ?)');
const insertEvent = db.prepare('INSERT INTO events (job_id, t, name, json) VALUES (?, ?, ?, ?)');
const closeServer = db.prepare('UPDATE servers SET closed_at = ?, players = 0 WHERE job_id = ?');

// Consecutive critical samples per server, so one bad sample doesn't page anyone.
const criticalStreak = new Map();

function storeSample(jobId, t, sample) {
  const s = sample.server ?? {};
  const p = sample.players ?? {};
  const e = sample.errors ?? {};
  const health = classify(sample);

  insertSample.run(
    jobId, t, numOrNull(s.players), numOrNull(s.fps), numOrNull(s.fpsMin), numOrNull(s.frameP99Ms), numOrNull(s.frameSpikes),
    numOrNull(s.heartbeatMs), numOrNull(s.physicsStepMs), numOrNull(s.memoryMb), numOrNull(s.luaHeapMb),
    numOrNull(s.network?.recvKbps), numOrNull(s.network?.sendKbps), numOrNull(s.counts?.instances),
    numOrNull(p.pingAvgMs), numOrNull(p.pingP95Ms), numOrNull(p.clientFpsAvg), numOrNull(p.lowFpsPlayers),
    numOrNull(e.errors), numOrNull(e.warnings), health.level, JSON.stringify(sample),
  );
  updateServerLive.run(
    numOrNull(s.players), numOrNull(s.maxPlayers), numOrNull(s.fps), numOrNull(s.heartbeatMs), numOrNull(s.memoryMb),
    numOrNull(p.pingAvgMs), numOrNull(p.clientFpsAvg), health.level, numOrNull(s.uptime), jobId,
  );

  for (const err of Array.isArray(e.top) ? e.top.slice(0, 10) : []) {
    insertError.run(jobId, t, 'server', str(err.message, 300), str(err.source), numOrNull(err.count) ?? 1);
  }
  for (const err of Array.isArray(p.clientErrors) ? p.clientErrors.slice(0, 10) : []) {
    insertError.run(jobId, t, 'client', str(err.message, 300), null, numOrNull(err.count) ?? 1);
  }

  const streak = health.level === 'critical' ? (criticalStreak.get(jobId) ?? 0) + 1 : 0;
  criticalStreak.set(jobId, streak);
  if (streak === 3) {
    bus.emit('alert', { type: 'critical', jobId, title: 'Server critical for 3 samples in a row', reasons: health.reasons });
  }
  if ((e.errors ?? 0) >= config.thresholds.errorSpikePerSample) {
    bus.emit('alert', { type: 'errors', jobId, title: `Error spike: ${e.errors} errors in one sample`, reasons: (e.top ?? []).slice(0, 3).map((x) => `${x.count}× ${x.message}`) });
  }
  bus.emit('sample', jobId, sample, health);
}

/** Handles a POST /ingest body. Throws on invalid input. */
export function ingest(body, ip) {
  const server = body?.server;
  const jobId = str(server?.jobId, 64);
  if (!jobId || !Array.isArray(body.records)) throw new Error('invalid payload');

  upsertServer.run(
    jobId, numOrNull(server.universeId), numOrNull(server.placeId), numOrNull(server.placeVersion),
    str(server.privateServerId, 64), server.reserved ? 1 : 0, numOrNull(server.startedAt) ?? now(), now(), ip,
  );

  db.exec('BEGIN');
  try {
    for (const record of body.records.slice(0, 1000)) {
      const t = numOrNull(record.t) ?? now();
      const data = record.data ?? {};
      switch (record.kind) {
        case 'sample':
          storeSample(jobId, t, data);
          break;
        case 'event':
          insertEvent.run(jobId, t, str(data.name, 100), JSON.stringify(data.fields ?? null));
          bus.emit('event', jobId, str(data.name, 100), data.fields ?? {});
          break;
        case 'lifecycle':
          if (data.event === 'shutdown') closeServer.run(t, jobId);
          bus.emit('lifecycle', jobId, data.event);
          break;
        default:
          break; // ignore unknown kinds (forward compatible)
      }
    }
    db.exec('COMMIT');
  } catch (err) {
    db.exec('ROLLBACK');
    throw err;
  }
  return { accepted: body.records.length };
}

/** Marks servers that stopped reporting without a shutdown as lost (possible crash). */
export function detectLostServers() {
  const cutoff = now() - config.staleSeconds;
  const lost = db.prepare('SELECT job_id, last_seen, players FROM servers WHERE closed_at IS NULL AND lost = 0 AND last_seen < ?').all(cutoff);
  for (const s of lost) {
    db.prepare('UPDATE servers SET lost = 1 WHERE job_id = ?').run(s.job_id);
    bus.emit('alert', {
      type: 'lost', jobId: s.job_id,
      title: 'Server stopped reporting',
      reasons: [`last seen <t:${s.last_seen}:R> with ${s.players ?? '?'} players (crash, hang or network issue)`],
    });
  }
}
