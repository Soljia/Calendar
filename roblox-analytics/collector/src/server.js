import { createServer } from 'node:http';
import { timingSafeEqual } from 'node:crypto';
import { config } from './config.js';
import { ingest } from './ingest.js';
import { captureStart, captureChunk, captureFinish } from './captures.js';
import { liveServers, overview } from './queries.js';

function authorised(req) {
  const header = req.headers.authorization ?? '';
  const expected = Buffer.from(`Bearer ${config.ingestKey}`);
  const given = Buffer.from(header);
  return config.ingestKey !== '' && given.length === expected.length && timingSafeEqual(given, expected);
}

function readJson(req) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const parts = [];
    req.on('data', (chunk) => {
      size += chunk.length;
      if (size > config.maxBodyBytes) {
        reject(Object.assign(new Error('payload too large'), { status: 413 }));
        req.destroy();
      } else parts.push(chunk);
    });
    req.on('end', () => {
      try {
        resolve(JSON.parse(Buffer.concat(parts).toString('utf8') || '{}'));
      } catch {
        reject(Object.assign(new Error('invalid JSON'), { status: 400 }));
      }
    });
    req.on('error', reject);
  });
}

function send(res, status, body) {
  res.writeHead(status, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify(body));
}

export function startHttpServer() {
  const server = createServer(async (req, res) => {
    try {
      const url = new URL(req.url, 'http://localhost');
      const ip = (req.headers['x-forwarded-for']?.split(',')[0] ?? req.socket.remoteAddress ?? '').trim();

      if (req.method === 'GET' && url.pathname === '/health') return send(res, 200, { ok: true });

      if (!authorised(req)) return send(res, 401, { error: 'unauthorised' });

      if (req.method === 'GET' && url.pathname === '/api/overview') return send(res, 200, overview());
      if (req.method === 'GET' && url.pathname === '/api/servers') return send(res, 200, liveServers());

      if (req.method !== 'POST') return send(res, 404, { error: 'not found' });
      const body = await readJson(req);

      if (url.pathname === '/ingest') return send(res, 200, ingest(body, ip));

      const m = url.pathname.match(/^\/capture\/([A-Za-z0-9-]{1,64})\/(start|chunk|finish)$/);
      if (m) {
        const [, id, action] = m;
        const jobId = typeof body.jobId === 'string' ? body.jobId.slice(0, 64) : null;
        if (!jobId) return send(res, 400, { error: 'jobId required' });
        if (action === 'start') return send(res, 200, captureStart(id, jobId, body));
        if (action === 'chunk') return send(res, 200, captureChunk(id, jobId, body));
        return send(res, 200, captureFinish(id, jobId, body));
      }
      return send(res, 404, { error: 'not found' });
    } catch (err) {
      const status = err.status ?? 400;
      if (status >= 500 || !err.status) console.warn('[http]', req.method, req.url, err.message);
      send(res, status, { error: err.message });
    }
  });
  server.listen(config.port, () => console.log(`[http] listening on :${config.port}`));
  return server;
}
