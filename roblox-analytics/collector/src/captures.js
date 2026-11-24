import { mkdirSync, writeFileSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { gzipSync } from 'node:zlib';
import { db } from './db.js';
import { config } from './config.js';
import { bus } from './ingest.js';

const ID_RE = /^[A-Za-z0-9-]{1,64}$/;
const capturesDir = join(config.dataDir, 'captures');
const now = () => Math.floor(Date.now() / 1000);

function dirFor(id) {
  if (!ID_RE.test(id)) throw new Error('invalid capture id');
  return join(capturesDir, id);
}

export function captureStart(id, jobId, body) {
  const dir = dirFor(id);
  mkdirSync(dir, { recursive: true });
  const duration = Math.min(Math.max(Number(body.duration) || 45, 1), 120);
  const requestedBy = String(body.requestedBy ?? '?').slice(0, 120);
  db.prepare(`INSERT OR REPLACE INTO captures (id, job_id, requested_by, duration, started_at, status, snapshot_json)
              VALUES (?, ?, ?, ?, ?, 'recording', ?)`).run(id, jobId, requestedBy, duration, now(), JSON.stringify(body.snapshot ?? null));
  bus.emit('captureStarted', { id, jobId, requestedBy, duration });
  return { ok: true };
}

export function captureChunk(id, jobId, body) {
  const dir = dirFor(id);
  const row = db.prepare('SELECT job_id FROM captures WHERE id = ?').get(id);
  if (!row || row.job_id !== jobId) throw new Error('unknown capture');
  const seq = Number(body.seq);
  if (!Number.isInteger(seq) || seq < 1 || seq > 1000) throw new Error('invalid seq');
  writeFileSync(join(dir, `chunk-${String(seq).padStart(4, '0')}.json`), JSON.stringify(body));
  db.prepare('UPDATE captures SET chunks = MAX(chunks, ?) WHERE id = ?').run(seq, id);
  return { ok: true };
}

/** Merges chunks in order into one raw capture object. */
export function mergeChunks(chunks) {
  const merged = {
    frames: { t: [], dt: [], simMs: [], heartbeatMs: [], physicsMs: [], luaMb: [] },
    heavy: [], zones: [], errors: [],
  };
  for (const c of chunks.sort((a, b) => a.seq - b.seq)) {
    for (const key of Object.keys(merged.frames)) {
      const column = c.frames?.[key];
      if (Array.isArray(column)) merged.frames[key].push(...column);
    }
    for (const key of ['heavy', 'zones', 'errors']) {
      if (Array.isArray(c[key])) merged[key].push(...c[key]);
    }
  }
  return merged;
}

/**
 * Converts a merged capture to Chrome Trace Event Format. Open the file in
 * https://ui.perfetto.dev or chrome://tracing.
 */
export function toTrace(raw, meta) {
  const pid = 1;
  const us = (seconds) => Math.round(seconds * 1e6);
  const events = [
    { ph: 'M', pid, name: 'process_name', args: { name: `Roblox server ${meta.jobId}` } },
    { ph: 'M', pid, tid: 1, name: 'thread_name', args: { name: 'Server frames (Heartbeat)' } },
    { ph: 'M', pid, tid: 2, name: 'thread_name', args: { name: 'Analytics.profile zones' } },
    { ph: 'M', pid, tid: 3, name: 'thread_name', args: { name: 'Script errors' } },
  ];

  const f = raw.frames;
  for (let i = 0; i < f.t.length; i++) {
    const t = f.t[i];
    const dtMs = f.dt[i];
    const ts = us(t - dtMs / 1000);
    events.push({
      ph: 'X', pid, tid: 1, name: dtMs > 1000 / 30 ? 'Frame (slow)' : 'Frame', ts, dur: Math.round(dtMs * 1000),
      args: { frameMs: dtMs, simulationMs: f.simMs[i], heartbeatMs: f.heartbeatMs[i], physicsStepMs: f.physicsMs[i] },
    });
    events.push({ ph: 'C', pid, name: 'Frame time (ms)', ts: us(t), args: { frame: dtMs, simulation: f.simMs[i] } });
    events.push({ ph: 'C', pid, name: 'Engine (ms)', ts: us(t), args: { heartbeat: f.heartbeatMs[i], physicsStep: f.physicsMs[i] } });
    events.push({ ph: 'C', pid, name: 'Lua heap (MB)', ts: us(t), args: { heap: f.luaMb[i] } });
  }

  for (const h of raw.heavy) {
    const ts = us(h.t);
    if (h.memoryByTag) events.push({ ph: 'C', pid, name: 'Memory by category (MB)', ts, args: h.memoryByTag });
    if (h.memoryMb != null) events.push({ ph: 'C', pid, name: 'Memory total (MB)', ts, args: { total: h.memoryMb } });
    if (h.network) events.push({ ph: 'C', pid, name: 'Network (kbps)', ts, args: h.network });
    if (h.counts) events.push({ ph: 'C', pid, name: 'Instances / physics', ts, args: h.counts });
    events.push({ ph: 'C', pid, name: 'Players', ts, args: { players: h.players ?? 0 } });
    if (h.pingAvgMs != null) events.push({ ph: 'C', pid, name: 'Ping (ms)', ts, args: { avg: h.pingAvgMs, max: h.pingMaxMs ?? h.pingAvgMs } });
  }

  for (const z of raw.zones) {
    events.push({ ph: 'X', pid, tid: 2, name: z.n, ts: us(z.t), dur: Math.max(1, Math.round(z.d * 1000)) });
  }
  for (const e of raw.errors) {
    events.push({ ph: 'i', s: 'g', pid, tid: 3, name: `Error: ${e.message}`, ts: us(e.t), args: { source: e.source } });
  }

  return { traceEvents: events, displayTimeUnit: 'ms', metadata: meta };
}

export function captureFinish(id, jobId, body) {
  const dir = dirFor(id);
  const row = db.prepare('SELECT * FROM captures WHERE id = ?').get(id);
  if (!row || row.job_id !== jobId) throw new Error('unknown capture');

  const chunks = readdirSync(dir)
    .filter((name) => name.startsWith('chunk-'))
    .map((name) => JSON.parse(readFileSync(join(dir, name), 'utf8')));
  const raw = mergeChunks(chunks);
  const summary = body.summary ?? {};
  const meta = {
    captureId: id, jobId, requestedBy: row.requested_by, startedAt: row.started_at,
    duration: row.duration, snapshot: JSON.parse(row.snapshot_json ?? 'null'), summary,
  };

  const rawPath = join(dir, `${id}.raw.json.gz`);
  writeFileSync(rawPath, gzipSync(JSON.stringify({ meta, ...raw })));

  // Discord's default upload limit is 10 MB; gzip the trace when it's big (Perfetto opens .gz directly).
  const traceJson = JSON.stringify(toTrace(raw, meta));
  let tracePath = join(dir, `${id}.trace.json`);
  if (traceJson.length > 8 * 1024 * 1024) {
    tracePath += '.gz';
    writeFileSync(tracePath, gzipSync(traceJson));
  } else {
    writeFileSync(tracePath, traceJson);
  }

  db.prepare(`UPDATE captures SET finished_at = ?, status = 'done', summary_json = ? WHERE id = ?`).run(now(), JSON.stringify(summary), id);
  const files = [tracePath, rawPath].map((path) => ({ path, bytes: statSync(path).size }));
  bus.emit('captureFinished', { id, jobId, requestedBy: row.requested_by, summary, files });
  return { ok: true, frames: raw.frames.t.length, files: files.map((x) => x.path) };
}
