import { config } from './config.js';

/**
 * Publishes a command to every live server through Open Cloud MessagingService.
 * The Roblox side (Analytics/init.lua) only acts if jobId matches its own.
 */
export async function publishCommand(command) {
  const { openCloudKey, universeId, messagingTopic } = config.roblox;
  if (!openCloudKey || !universeId) throw new Error('Open Cloud is not configured (ROBLOX_OPEN_CLOUD_KEY / ROBLOX_UNIVERSE_ID)');
  const url = `https://apis.roblox.com/messaging-service/v1/universes/${encodeURIComponent(universeId)}/topics/${encodeURIComponent(messagingTopic)}`;
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'x-api-key': openCloudKey, 'Content-Type': 'application/json' },
    body: JSON.stringify({ message: JSON.stringify(command) }),
  });
  if (!res.ok) throw new Error(`Open Cloud ${res.status}: ${(await res.text()).slice(0, 200)}`);
}
