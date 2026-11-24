import { config } from './config.js';
import { pruneOldData } from './db.js';
import { detectLostServers } from './ingest.js';
import { startHttpServer } from './server.js';
import { startDiscord } from './discord.js';

if (!config.ingestKey) {
  console.error('INGEST_KEY is required (copy .env.example to .env)');
  process.exit(1);
}

startHttpServer();
setInterval(detectLostServers, 30_000);
setInterval(pruneOldData, 60 * 60_000);
pruneOldData();

startDiscord().catch((err) => console.error('[discord] failed to start:', err.message));
