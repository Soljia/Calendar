// Builds Discord embed objects (plain JSON, accepted by discord.js and webhooks).
import { healthEmoji } from './health.js';
import { sparkline } from './queries.js';

const COLOR = { healthy: 0x2ecc71, degraded: 0xf1c40f, critical: 0xe74c3c, info: 0x3498db, lost: 0x2c2f33 };
const HEALTH_ORDER = { critical: 0, degraded: 1, healthy: 2 };

export const fmt = (n, digits = 0) => (n == null ? '—' : Number(n).toLocaleString('en-US', { maximumFractionDigits: digits, minimumFractionDigits: digits }));
export function fmtDuration(seconds) {
  if (seconds == null) return '—';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return h > 0 ? `${h}h${String(m).padStart(2, '0')}m` : `${m}m`;
}
const short = (jobId) => String(jobId).slice(0, 8);
const pad = (s, n) => String(s).padEnd(n).slice(0, n);
const lpad = (s, n) => String(s).padStart(n).slice(-n);

function perfLabel(fps) {
  if (fps == null) return '';
  if (fps >= 55) return 'Excellent';
  if (fps >= 45) return 'Good';
  if (fps >= 30) return 'Fair';
  return 'Poor';
}

// ---------------------------------------------------------------- public ---
export function publicStatusEmbed(gameName, o, trend) {
  const overall = o.health.critical > 0 ? 'degraded' : o.health.degraded > Math.max(1, o.servers * 0.1) ? 'degraded' : 'healthy';
  const statusLine = o.servers === 0
    ? '⚫ **No servers online**'
    : overall === 'healthy' ? '🟢 **All systems normal**' : '🟡 **Some servers are running slowly. We’re on it.**';
  return {
    title: `🎮 ${gameName}: Live Status`,
    description: statusLine,
    color: o.servers === 0 ? COLOR.lost : COLOR[overall],
    fields: [
      { name: '👥 Players online', value: `**${fmt(o.players)}**`, inline: true },
      { name: '🖥️ Servers', value: `**${fmt(o.servers)}**`, inline: true },
      { name: '📈 Peak today', value: `**${fmt(o.peakToday)}**`, inline: true },
      { name: '⚡ Server performance', value: `${fmt(o.avgServerFps, 1)} FPS · ${perfLabel(o.avgServerFps)}`, inline: true },
      { name: '📶 Average ping', value: `${fmt(o.avgPingMs)} ms`, inline: true },
      { name: '🎞️ Player FPS', value: `${fmt(o.avgClientFps)} avg`, inline: true },
      { name: 'Players, last 24 h', value: '```\n' + sparkline(trend) + '\n```' },
    ],
    footer: { text: `Version ${o.versions[0] ?? '—'} · updates every minute` },
    timestamp: new Date().toISOString(),
  };
}

// ----------------------------------------------------------------- admin ---
export function adminFleetEmbed(o, servers, errors, platforms) {
  const sorted = [...servers].sort((a, b) =>
    (HEALTH_ORDER[a.health] ?? 3) - (HEALTH_ORDER[b.health] ?? 3) || (a.fps ?? 99) - (b.fps ?? 99));
  const header = '   JOB      PLR   FPS  HB ms  MEM MB  PING  UP';
  const rows = sorted.slice(0, 12).map((s) =>
    `${healthEmoji[s.health] ?? '⚪'} ${pad(short(s.job_id), 8)} ${lpad(`${s.players ?? 0}/${s.max_players ?? '?'}`, 5)} ${lpad(fmt(s.fps, 1), 5)} ${lpad(fmt(s.heartbeat_ms, 1), 6)} ${lpad(fmt(s.memory_mb), 7)} ${lpad(fmt(s.ping_avg), 5)}  ${fmtDuration(s.uptime)}`);
  if (sorted.length > 12) rows.push(`   … ${sorted.length - 12} more healthy servers`);

  const totalPlatform = Object.values(platforms).reduce((a, b) => a + b, 0) || 1;
  const platformLine = Object.entries(platforms).sort((a, b) => b[1] - a[1])
    .map(([k, v]) => `${k} ${Math.round((v / totalPlatform) * 100)}%`).join(' · ') || '—';

  const versionCounts = {};
  for (const s of servers) versionCounts[s.place_version] = (versionCounts[s.place_version] ?? 0) + 1;
  const newest = o.versions[0];
  const versionLine = Object.entries(versionCounts).sort((a, b) => b[0] - a[0])
    .map(([v, n]) => `v${v}: ${n} server${n === 1 ? '' : 's'}${Number(v) !== newest ? ' ⚠️ outdated' : ''}`).join('\n') || '—';

  const errorLines = errors.map((e) =>
    `\`${lpad(fmt(e.total), 5)}×\` ${e.origin === 'client' ? '📱' : '🖥️'} ${String(e.message).slice(0, 90)}${e.servers > 1 ? ` *(${e.servers} servers)*` : ''}`);

  const worst = o.health.critical > 0 ? 'critical' : o.health.degraded > 0 ? 'degraded' : 'healthy';
  return {
    title: '🛠️ Server Fleet: Admin View',
    description: `🟢 ${o.health.healthy}  🟡 ${o.health.degraded}  🔴 ${o.health.critical}  ·  **${fmt(o.players)}** players on **${o.servers}** servers\n` +
      `Avg memory **${fmt(o.avgMemoryMb)} MB** · Avg ping **${fmt(o.avgPingMs)} ms** · Captures today **${o.capturesToday}**`,
    color: COLOR[worst],
    fields: [
      { name: 'Servers (worst first)', value: '```\n' + [header, ...rows].join('\n').slice(0, 1000) + '\n```' },
      { name: 'Top errors, last hour', value: errorLines.join('\n').slice(0, 1024) || 'None 🎉' },
      { name: 'Platforms', value: platformLine, inline: true },
      { name: 'Versions', value: versionLine.slice(0, 1024), inline: true },
    ],
    footer: { text: 'Use /server <job> for details · /capture <job> to record a 30–60 s profile' },
    timestamp: new Date().toISOString(),
  };
}

export function serverDetailEmbed(server, sample, history) {
  const s = sample?.server ?? {};
  const p = sample?.players ?? {};
  const m = sample?.metrics ?? {};
  const topMemory = Object.entries(s.memoryByTag ?? {}).sort((a, b) => b[1] - a[1]).slice(0, 6)
    .map(([k, v]) => `${k}: ${fmt(v)} MB`).join('\n') || '—';
  const remotes = Object.entries(m.remotes ?? {}).sort((a, b) => b[1].callsPerSec - a[1].callsPerSec).slice(0, 5)
    .map(([k, v]) => `${k.split('.').pop()}: ${v.callsPerSec}/s (top caller ${v.topCallerPerSec}/s)`).join('\n') || '—';
  const timings = Object.entries(m.timings ?? {}).sort((a, b) => (b[1].p95Ms ?? 0) - (a[1].p95Ms ?? 0)).slice(0, 5)
    .map(([k, v]) => `${k}: p95 ${v.p95Ms} ms, max ${v.maxMs} ms`).join('\n') || '—';
  const budgets = Object.entries(s.dataStoreBudget ?? {}).map(([k, v]) => `${k.replace('Async', '')} ${v}`).join(' · ') || '—';

  return {
    title: `${healthEmoji[server.health] ?? '⚪'} Server ${short(server.job_id)}`,
    description: `\`${server.job_id}\`\nPlace version **${server.place_version}** · up **${fmtDuration(server.uptime)}**` +
      `${server.private_server_id ? ' · private server' : ''}${server.reserved ? ' · reserved' : ''}`,
    color: COLOR[server.health] ?? COLOR.info,
    fields: [
      { name: '👥 Players', value: `${s.players ?? 0}/${s.maxPlayers ?? '?'}\n+${p.joins ?? 0} / −${p.leaves ?? 0} last sample`, inline: true },
      { name: '⚡ Frame', value: `${fmt(s.fps, 1)} FPS (min ${fmt(s.fpsMin, 1)})\np99 ${fmt(s.frameP99Ms, 1)} ms · ${s.frameSpikes ?? 0} spikes`, inline: true },
      { name: '⚙️ Engine', value: `Heartbeat ${fmt(s.heartbeatMs, 2)} ms\nPhysics ${fmt(s.physicsStepMs, 2)} ms @ ${fmt(s.physicsFps)} Hz`, inline: true },
      { name: '🧠 Memory', value: `${fmt(s.memoryMb)} MB total\nLua heap ${fmt(s.luaHeapMb, 1)} MB`, inline: true },
      { name: '🌐 Network', value: `↓ ${fmt(s.network?.recvKbps, 1)} kbps\n↑ ${fmt(s.network?.sendKbps, 1)} kbps`, inline: true },
      { name: '📶 Players’ connection', value: `Ping ${fmt(p.pingAvgMs)} ms (p95 ${fmt(p.pingP95Ms)})\nClient FPS ${fmt(p.clientFpsAvg)} · ${p.lowFpsPlayers ?? 0} under 30`, inline: true },
      { name: '🧱 World', value: `${fmt(s.counts?.instances)} instances\n${fmt(s.counts?.awakeParts)} awake parts · ${fmt(s.counts?.contacts)} contacts`, inline: true },
      { name: 'Memory by category', value: topMemory, inline: true },
      { name: 'Slowest profiled code', value: timings, inline: true },
      { name: 'Busiest remotes', value: remotes },
      { name: 'DataStore budget', value: budgets },
      { name: 'Last 30 min', value: '```\nFPS     ' + sparkline(history.map((h) => h.fps ?? 0)) + '\nPlayers ' + sparkline(history.map((h) => h.players ?? 0)) + '\nMemory  ' + sparkline(history.map((h) => h.memory_mb ?? 0)) + '\n```' },
    ],
    timestamp: new Date().toISOString(),
  };
}

// ------------------------------------------------------- alerts/captures ---
export function alertEmbed(alert) {
  const color = alert.type === 'critical' ? COLOR.critical : alert.type === 'lost' ? COLOR.lost : COLOR.degraded;
  const icon = alert.type === 'critical' ? '🔴' : alert.type === 'lost' ? '⚫' : '🟡';
  return {
    title: `${icon} ${alert.title}`,
    description: `Server \`${alert.jobId}\`\n` + (alert.reasons ?? []).map((r) => `• ${r}`).join('\n'),
    color,
    timestamp: new Date().toISOString(),
  };
}

export function captureStartedEmbed(c) {
  return {
    title: '⏺️ Recording performance capture',
    description: `Capture \`${c.id}\` on server \`${short(c.jobId)}\` for **${c.duration}s**\nRequested by ${c.requestedBy}`,
    color: COLOR.info,
    timestamp: new Date().toISOString(),
  };
}

export function captureFinishedEmbed(c) {
  const s = c.summary ?? {};
  const worst = (s.worstFrames ?? []).slice(0, 5).map((f) => `${fmt(f.ms, 1)} ms at ${fmt(f.t, 1)}s`).join('\n') || '—';
  const zones = (s.topZones ?? []).slice(0, 6).map((z) => `${z.name}: ${fmt(z.totalMs, 1)} ms total, ${z.calls} calls, max ${fmt(z.maxMs, 2)} ms`).join('\n') || 'No Analytics.profile zones recorded';
  const bad = (s.frameP99Ms ?? 0) > 50 || (s.fpsAvg ?? 60) < 40;
  return {
    title: `✅ Capture ready: ${c.id}`,
    description: `Server \`${short(c.jobId)}\` · ${fmt(s.seconds, 1)}s · ${fmt(s.frames)} frames · requested by ${c.requestedBy}\n` +
      'Open the **.trace.json** in https://ui.perfetto.dev (or chrome://tracing). The **.raw.json.gz** has every sample for your own analysis.',
    color: bad ? COLOR.degraded : COLOR.healthy,
    fields: [
      { name: 'Frame time', value: `avg ${fmt(s.fpsAvg, 1)} FPS\np50 ${fmt(s.frameP50Ms, 1)} ms · p99 ${fmt(s.frameP99Ms, 1)} ms\n${s.frameSpikes ?? 0} frames over 33 ms`, inline: true },
      { name: 'Memory', value: `${fmt(s.memoryStartMb)} → ${fmt(s.memoryEndMb)} MB`, inline: true },
      { name: 'Errors during capture', value: `${s.errors ?? 0}`, inline: true },
      { name: 'Worst frames', value: worst, inline: true },
      { name: 'Most expensive zones', value: zones.slice(0, 1024) },
    ],
    timestamp: new Date().toISOString(),
  };
}
