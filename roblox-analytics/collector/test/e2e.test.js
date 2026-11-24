// End-to-end: simulated Roblox servers -> HTTP collector -> SQLite -> trace + embeds.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const dataDir = mkdtempSync(join(tmpdir(), 'rbx-analytics-'));
process.env.DATA_DIR = dataDir;
process.env.INGEST_KEY = 'test-key';
process.env.PORT = '0';

const { bus } = await import('../src/ingest.js');
const { startHttpServer } = await import('../src/server.js');
const queries = await import('../src/queries.js');
const embeds = await import('../src/embeds.js');

let server, base;
before(async () => {
  server = startHttpServer();
  await new Promise((r) => server.once('listening', r));
  base = `http://127.0.0.1:${server.address().port}`;
});
after(() => { server.close(); rmSync(dataDir, { recursive: true, force: true }); });

const post = (path, body, key = 'test-key') => fetch(base + path, {
  method: 'POST', headers: { Authorization: `Bearer ${key}`, 'Content-Type': 'application/json' }, body: JSON.stringify(body),
});

function sample(players, fps, memoryMb, errors = 0) {
  return {
    server: {
      uptime: 3600, players, maxPlayers: 30, fps, fpsMin: fps - 10, frameP99Ms: 1000 / (fps - 5), frameMaxMs: 80, frameSpikes: fps < 40 ? 25 : 0,
      heartbeatMs: 1000 / fps * 0.4, physicsStepMs: 2.1, physicsFps: 60, memoryMb, luaHeapMb: 120,
      memoryByTag: { Instances: 400, LuaHeap: 120, PhysicsParts: 80, Sounds: 30 },
      network: { recvKbps: 40, sendKbps: 210, physRecvKbps: 5, physSendKbps: 60 },
      counts: { instances: 85000, primitives: 9000, movingPrimitives: 120, contacts: 300, awakeParts: 140 },
      dataStoreBudget: { GetAsync: 300, SetIncrementAsync: 300, UpdateAsync: 300 },
    },
    players: { count: players, joins: 2, leaves: 1, pingAvgMs: 80, pingP95Ms: 160, clientFpsAvg: 55, lowFpsPlayers: 1,
      platforms: { Desktop: Math.ceil(players / 2), Mobile: Math.floor(players / 2) }, clientErrors: [] },
    errors: { errors, warnings: 3, top: errors ? [{ message: 'Workspace.NPC.AI:N: attempt to index nil with Humanoid', count: errors, source: 'Workspace.NPC.AI' }] : [] },
    metrics: { counters: {}, gauges: {}, timings: { 'NPC.Update': { count: 300, avgMs: 0.8, p95Ms: 2.4, maxMs: 6.1 } },
      remotes: { 'ReplicatedStorage.Remotes.Shoot': { callsPerSec: 14, kbps: 1.2, topCallerPerSec: 4 } } },
    transport: { sent: 10, failed: 0 },
  };
}

test('rejects bad key', async () => {
  const res = await post('/ingest', {}, 'wrong');
  assert.equal(res.status, 401);
});

test('ingests samples, classifies health, raises alerts', async () => {
  const alerts = [];
  bus.on('alert', (a) => alerts.push(a));
  const servers = [
    ['aaaa1111-0000-0000-0000-000000000001', 28, 59.9, 2100, 0],
    ['bbbb2222-0000-0000-0000-000000000002', 22, 46.0, 3200, 2],
    ['cccc3333-0000-0000-0000-000000000003', 30, 24.0, 5700, 25],
  ];
  for (const [jobId, players, fps, mem, errs] of servers) {
    const records = [0, 1, 2].map((i) => ({ kind: 'sample', t: Math.floor(Date.now() / 1000) - 10 + i * 5, data: sample(players, fps, mem, errs) }));
    records.unshift({ kind: 'lifecycle', t: Math.floor(Date.now() / 1000) - 20, data: { event: 'start' } });
    const res = await post('/ingest', { server: { jobId, placeId: 1, universeId: 2, placeVersion: 312, startedAt: 0 }, records });
    assert.equal(res.status, 200);
  }
  const live = queries.liveServers();
  assert.equal(live.length, 3);
  assert.deepEqual(live.map((s) => s.health).sort(), ['critical', 'degraded', 'healthy']);
  assert.ok(alerts.some((a) => a.type === 'critical' && a.jobId.startsWith('cccc')));
  assert.ok(alerts.some((a) => a.type === 'errors'));

  const o = queries.overview();
  assert.equal(o.players, 80);
  assert.equal(o.peakToday, 80);
});

test('capture start -> chunks -> finish produces a valid trace', async () => {
  const jobId = 'aaaa1111-0000-0000-0000-000000000001';
  const id = 'aaaa1111-1700000000-beef';
  let finished;
  bus.once('captureFinished', (c) => { finished = c; });

  assert.equal((await post(`/capture/${id}/start`, { jobId, duration: 30, requestedBy: 'tester', snapshot: {} })).status, 200);
  for (let seq = 1; seq <= 3; seq++) {
    const t0 = (seq - 1) * 10;
    const n = 600;
    const frames = { t: [], dt: [], simMs: [], heartbeatMs: [], physicsMs: [], luaMb: [] };
    for (let i = 0; i < n; i++) {
      frames.t.push(t0 + (i + 1) / 60); frames.dt.push(i === 300 ? 120 : 16.7);
      frames.simMs.push(3); frames.heartbeatMs.push(6); frames.physicsMs.push(2); frames.luaMb.push(120);
    }
    const body = { jobId, seq, final: seq === 3, frames,
      heavy: [{ t: t0, memoryMb: 2100, memoryByTag: { Instances: 400 }, network: { recvKbps: 40 }, counts: { instances: 85000 }, players: 28, pingAvgMs: 80, pingMaxMs: 200 }],
      zones: [{ n: 'NPC.Update', t: t0 + 1, d: 2.5 }], errors: seq === 2 ? [{ t: 15, message: 'boom', source: 'X' }] : [] };
    assert.equal((await post(`/capture/${id}/chunk`, body)).status, 200);
  }
  const res = await post(`/capture/${id}/finish`, { jobId, summary: { frames: 1800, seconds: 30, fpsAvg: 59.1, frameP50Ms: 16.7, frameP99Ms: 18, frameSpikes: 3,
    worstFrames: [{ t: 5.0, ms: 120 }], topZones: [{ name: 'NPC.Update', calls: 3, totalMs: 7.5, maxMs: 2.5 }], errors: 1, memoryStartMb: 2100, memoryEndMb: 2104, chunks: 3 } });
  const out = await res.json();
  assert.equal(res.status, 200, JSON.stringify(out));
  assert.equal(out.frames, 1800);

  const trace = JSON.parse(readFileSync(finished.files[0].path, 'utf8'));
  const frameEvents = trace.traceEvents.filter((e) => e.ph === 'X' && e.tid === 1);
  assert.equal(frameEvents.length, 1800);
  assert.equal(frameEvents.filter((e) => e.name === 'Frame (slow)').length, 3);
  assert.ok(trace.traceEvents.some((e) => e.ph === 'i' && e.name === 'Error: boom'));
});

test('capture rejects path traversal and mismatched job', async () => {
  assert.equal((await post('/capture/..%2F..%2Fetc/start', { jobId: 'x' })).status, 404);
  assert.equal((await post('/capture/aaaa1111-1700000000-beef/chunk', { jobId: 'someone-else', seq: 9 })).status, 400);
});

test('embeds render within Discord limits', () => {
  const o = queries.overview();
  const all = [
    embeds.publicStatusEmbed('Test Game', o, queries.playerTrend(24, 24)),
    embeds.adminFleetEmbed(o, queries.liveServers(), queries.recentErrors(60, 8), queries.platformMix(10)),
    embeds.serverDetailEmbed(queries.liveServers()[0], queries.latestSample(queries.liveServers()[0].job_id), queries.serverHistory(queries.liveServers()[0].job_id)),
  ];
  for (const e of all) {
    assert.ok(e.title.length <= 256);
    assert.ok(e.fields.length <= 25);
    for (const f of e.fields) assert.ok(f.value.length <= 1024 && f.value.length > 0, `${e.title} / ${f.name}`);
  }
  if (process.env.PRINT_EMBEDS) console.log(JSON.stringify(all, null, 2));
});
