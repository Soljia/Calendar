import {
  Client, GatewayIntentBits, SlashCommandBuilder, PermissionFlagsBits, AttachmentBuilder,
  ActionRowBuilder, ButtonBuilder, ButtonStyle, MessageFlags, Events,
} from 'discord.js';
import { config } from './config.js';
import { kv } from './db.js';
import { bus } from './ingest.js';
import { overview, liveServers, findServer, latestSample, serverHistory, playerTrend, recentErrors, platformMix } from './queries.js';
import {
  publicStatusEmbed, adminFleetEmbed, serverDetailEmbed, alertEmbed, captureStartedEmbed, captureFinishedEmbed,
} from './embeds.js';
import { publishCommand } from './roblox.js';

const d = config.discord;
const ALERT_COOLDOWN_MS = 15 * 60 * 1000;
const alertSentAt = new Map();

const commands = [
  new SlashCommandBuilder().setName('status').setDescription('Live game status'),
  new SlashCommandBuilder().setName('servers').setDescription('Admin: all live servers')
    .setDefaultMemberPermissions(PermissionFlagsBits.ManageGuild),
  new SlashCommandBuilder().setName('server').setDescription('Admin: detailed stats for one server')
    .setDefaultMemberPermissions(PermissionFlagsBits.ManageGuild)
    .addStringOption((o) => o.setName('job').setDescription('Job id (or first characters)').setRequired(true).setAutocomplete(true)),
  new SlashCommandBuilder().setName('errors').setDescription('Admin: top script errors')
    .setDefaultMemberPermissions(PermissionFlagsBits.ManageGuild)
    .addIntegerOption((o) => o.setName('minutes').setDescription('Look-back window').setMinValue(5).setMaxValue(1440)),
  new SlashCommandBuilder().setName('capture').setDescription('Admin: record a 30–60 s detailed performance capture')
    .setDefaultMemberPermissions(PermissionFlagsBits.ManageGuild)
    .addStringOption((o) => o.setName('job').setDescription('Job id (or first characters)').setRequired(true).setAutocomplete(true))
    .addIntegerOption((o) => o.setName('seconds').setDescription('30–60 (default 45)').setMinValue(30).setMaxValue(60)),
].map((c) => c.toJSON());

function isAdmin(interaction) {
  if (interaction.memberPermissions?.has(PermissionFlagsBits.Administrator)) return true;
  return Boolean(d.adminRoleId && interaction.member?.roles?.cache?.has(d.adminRoleId));
}

const captureButton = (jobId) => new ActionRowBuilder().addComponents(
  new ButtonBuilder().setCustomId(`capture:${jobId}:45`).setLabel('Record 45 s capture').setEmoji('⏺️').setStyle(ButtonStyle.Secondary),
);

async function requestCapture(interaction, jobPrefix, seconds) {
  const server = findServer(jobPrefix);
  if (!server || server.closed_at || server.lost) {
    return interaction.reply({ content: `No live server matches \`${jobPrefix}\`.`, flags: MessageFlags.Ephemeral });
  }
  try {
    await publishCommand({ action: 'capture', jobId: server.job_id, seconds, requestedBy: `${interaction.user.tag}` });
    return interaction.reply({
      content: `⏺️ Asked server \`${server.job_id.slice(0, 8)}\` to record ${seconds}s. Results will appear in <#${d.captureChannelId}>.`,
      flags: MessageFlags.Ephemeral,
    });
  } catch (err) {
    return interaction.reply({ content: `Couldn’t reach Roblox: ${err.message}`, flags: MessageFlags.Ephemeral });
  }
}

/** Posts a message once, then keeps editing it (message id persisted in kv). */
async function upsertPinned(client, channelId, key, payload) {
  if (!channelId) return;
  const channel = await client.channels.fetch(channelId).catch(() => null);
  if (!channel?.isTextBased()) return;
  const messageId = kv.get(key);
  if (messageId) {
    const message = await channel.messages.fetch(messageId).catch(() => null);
    if (message) return message.edit(payload);
  }
  const message = await channel.send(payload);
  kv.set(key, message.id);
}

async function send(client, channelId, payload) {
  if (!channelId) return;
  const channel = await client.channels.fetch(channelId).catch(() => null);
  if (channel?.isTextBased()) await channel.send(payload).catch((err) => console.warn('[discord] send failed:', err.message));
}

async function refreshStatus(client) {
  const o = overview();
  await upsertPinned(client, d.publicStatusChannelId, 'msg:public', { embeds: [publicStatusEmbed(config.gameName, o, playerTrend(24, 24))] });
  await upsertPinned(client, d.adminStatusChannelId, 'msg:admin', {
    embeds: [adminFleetEmbed(o, liveServers(), recentErrors(60, 8), platformMix(10))],
  });
}

export async function startDiscord() {
  if (!d.token) {
    console.log('[discord] DISCORD_TOKEN not set; bot disabled');
    return null;
  }
  const client = new Client({ intents: [GatewayIntentBits.Guilds] });

  client.once(Events.ClientReady, async () => {
    console.log(`[discord] logged in as ${client.user.tag}`);
    if (d.guildId) {
      const guild = await client.guilds.fetch(d.guildId);
      await guild.commands.set(commands);
    }
    const tick = () => refreshStatus(client).catch((err) => console.warn('[discord] status refresh failed:', err.message));
    tick();
    setInterval(tick, d.updateSeconds * 1000);
  });

  client.on(Events.InteractionCreate, async (interaction) => {
    try {
      if (interaction.isAutocomplete()) {
        const typed = String(interaction.options.getFocused()).toLowerCase();
        const choices = liveServers().filter((s) => s.job_id.toLowerCase().startsWith(typed)).slice(0, 25)
          .map((s) => ({ name: `${s.job_id.slice(0, 8)} · ${s.players ?? 0} players · ${s.fps ?? '?'} FPS · ${s.health}`, value: s.job_id }));
        return interaction.respond(choices);
      }

      if (interaction.isButton()) {
        const [kind, jobId, seconds] = interaction.customId.split(':');
        if (kind !== 'capture') return;
        if (!isAdmin(interaction)) return interaction.reply({ content: 'Admins only.', flags: MessageFlags.Ephemeral });
        return requestCapture(interaction, jobId, Number(seconds));
      }

      if (!interaction.isChatInputCommand()) return;
      const name = interaction.commandName;

      if (name === 'status') {
        return interaction.reply({ embeds: [publicStatusEmbed(config.gameName, overview(), playerTrend(24, 24))] });
      }
      if (!isAdmin(interaction)) return interaction.reply({ content: 'Admins only.', flags: MessageFlags.Ephemeral });

      if (name === 'servers') {
        return interaction.reply({ embeds: [adminFleetEmbed(overview(), liveServers(), recentErrors(60, 8), platformMix(10))], flags: MessageFlags.Ephemeral });
      }
      if (name === 'server') {
        const server = findServer(interaction.options.getString('job', true));
        if (!server) return interaction.reply({ content: 'No such server.', flags: MessageFlags.Ephemeral });
        return interaction.reply({
          embeds: [serverDetailEmbed(server, latestSample(server.job_id), serverHistory(server.job_id, 30))],
          components: server.closed_at ? [] : [captureButton(server.job_id)],
          flags: MessageFlags.Ephemeral,
        });
      }
      if (name === 'errors') {
        const minutes = interaction.options.getInteger('minutes') ?? 60;
        const rows = recentErrors(minutes, 15);
        const text = rows.map((e) => `\`${e.total}×\` ${e.origin === 'client' ? '📱' : '🖥️'} **${e.message.slice(0, 150)}**${e.source ? `\n  ↳ ${e.source}` : ''}`).join('\n');
        return interaction.reply({ content: `**Top errors, last ${minutes} min**\n${text || 'None 🎉'}`.slice(0, 2000), flags: MessageFlags.Ephemeral });
      }
      if (name === 'capture') {
        return requestCapture(interaction, interaction.options.getString('job', true), interaction.options.getInteger('seconds') ?? 45);
      }
    } catch (err) {
      console.warn('[discord] interaction failed:', err);
      if (interaction.isRepliable() && !interaction.replied) {
        interaction.reply({ content: 'Something went wrong.', flags: MessageFlags.Ephemeral }).catch(() => {});
      }
    }
  });

  // --- push notifications from the collector ---
  bus.on('alert', (alert) => {
    const key = `${alert.type}:${alert.jobId}`;
    if (Date.now() - (alertSentAt.get(key) ?? 0) < ALERT_COOLDOWN_MS) return;
    alertSentAt.set(key, Date.now());
    send(client, d.alertChannelId, {
      embeds: [alertEmbed(alert)],
      components: alert.type === 'lost' ? [] : [captureButton(alert.jobId)],
    });
  });

  bus.on('captureStarted', (c) => send(client, d.captureChannelId, { embeds: [captureStartedEmbed(c)] }));

  bus.on('captureFinished', (c) => {
    const limit = 10 * 1024 * 1024;
    const files = c.files.filter((f) => f.bytes < limit).map((f) => new AttachmentBuilder(f.path));
    const skipped = c.files.filter((f) => f.bytes >= limit).map((f) => f.path);
    send(client, d.captureChannelId, {
      embeds: [captureFinishedEmbed(c)],
      files,
      content: skipped.length ? `Some files were too large to attach and are on the collector: ${skipped.join(', ')}` : undefined,
    });
  });

  bus.on('event', (jobId, name, fields) => {
    if (name === 'CaptureRequest' && fields && fields.ok === false) {
      send(client, d.captureChannelId, { content: `⚠️ Server \`${jobId.slice(0, 8)}\` refused the capture: ${fields.result}` });
    }
  });

  await client.login(d.token);
  return client;
}
