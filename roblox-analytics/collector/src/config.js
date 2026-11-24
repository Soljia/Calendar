const env = process.env;
const num = (name, fallback) => (env[name] !== undefined && env[name] !== '' ? Number(env[name]) : fallback);

export const config = {
  port: num('PORT', 8080),
  ingestKey: env.INGEST_KEY || '',
  dataDir: env.DATA_DIR || './data',
  retentionDays: num('RETENTION_DAYS', 14),
  gameName: env.GAME_NAME || 'Roblox Game',
  maxBodyBytes: 5 * 1024 * 1024,

  // A server that hasn't reported for this long (and didn't send "shutdown") is considered lost.
  staleSeconds: num('STALE_SECONDS', 120),

  thresholds: {
    fpsDegraded: num('FPS_DEGRADED', 50),
    fpsCritical: num('FPS_CRITICAL', 30),
    memoryDegradedMb: num('MEMORY_DEGRADED_MB', 4000),
    memoryCriticalMb: num('MEMORY_CRITICAL_MB', 5500),
    pingDegradedMs: num('PING_DEGRADED_MS', 250),
    errorSpikePerSample: num('ERROR_SPIKE_PER_SAMPLE', 20),
  },

  discord: {
    token: env.DISCORD_TOKEN || '',
    guildId: env.DISCORD_GUILD_ID || '',
    publicStatusChannelId: env.PUBLIC_STATUS_CHANNEL_ID || '',
    adminStatusChannelId: env.ADMIN_STATUS_CHANNEL_ID || '',
    alertChannelId: env.ALERT_CHANNEL_ID || '',
    captureChannelId: env.CAPTURE_CHANNEL_ID || '',
    adminRoleId: env.ADMIN_ROLE_ID || '',
    updateSeconds: num('STATUS_UPDATE_SECONDS', 60),
  },

  roblox: {
    openCloudKey: env.ROBLOX_OPEN_CLOUD_KEY || '',
    universeId: env.ROBLOX_UNIVERSE_ID || '',
    messagingTopic: env.ROBLOX_MESSAGING_TOPIC || 'AnalyticsCommand',
  },
};
