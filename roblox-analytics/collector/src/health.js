import { config } from './config.js';

const t = config.thresholds;

/**
 * Classifies a server from one sample.
 * Returns { level: 'healthy'|'degraded'|'critical', reasons: string[] }.
 */
export function classify(sample) {
  const s = sample.server ?? {};
  const p = sample.players ?? {};
  const e = sample.errors ?? {};
  const critical = [];
  const degraded = [];

  if (s.fps != null && s.fps < t.fpsCritical) critical.push(`server FPS ${s.fps}`);
  else if (s.fps != null && s.fps < t.fpsDegraded) degraded.push(`server FPS ${s.fps}`);

  if (s.memoryMb != null && s.memoryMb > t.memoryCriticalMb) critical.push(`memory ${Math.round(s.memoryMb)} MB`);
  else if (s.memoryMb != null && s.memoryMb > t.memoryDegradedMb) degraded.push(`memory ${Math.round(s.memoryMb)} MB`);

  if (p.pingP95Ms != null && p.pingP95Ms > t.pingDegradedMs) degraded.push(`p95 ping ${p.pingP95Ms} ms`);
  if ((s.frameSpikes ?? 0) > 10) degraded.push(`${s.frameSpikes} frame spikes`);
  if ((e.errors ?? 0) >= t.errorSpikePerSample) degraded.push(`${e.errors} script errors`);

  if (critical.length) return { level: 'critical', reasons: [...critical, ...degraded] };
  if (degraded.length) return { level: 'degraded', reasons: degraded };
  return { level: 'healthy', reasons: [] };
}

export const healthEmoji = { healthy: '🟢', degraded: '🟡', critical: '🔴', lost: '⚫' };
