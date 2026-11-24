--!strict
-- Analytics configuration. Edit this file per experience.

local Config = {
	-- Base URL of the collector service (see /collector). No trailing slash.
	Endpoint = "https://your-collector.example.com",

	-- Auth: in live servers the key is read from the Secrets Store
	-- (Creator Hub > Experience > Secrets) under this name. The secret's domain
	-- must match the Endpoint host. StudioKey is used only when running in Studio.
	SecretName = "analytics_key",
	StudioKey = "dev-only-key",

	EnableInStudio = false, -- set true to send data while play-testing

	-- General (always-on) monitoring
	SampleInterval = 5, -- seconds between server samples
	FlushInterval = 30, -- seconds between batched uploads
	MaxQueue = 500, -- records kept in memory if the collector is unreachable
	MaxRequestsPerMinute = 60, -- self-imposed; Roblox allows ~500/min per server for ALL HttpService use

	-- Client reports (FPS, client memory, platform, client errors)
	ClientReportInterval = 10,
	IncludeUserIds = false, -- keep player identities out of analytics by default

	-- Who may run admin commands (/capture etc.). The experience owner (user or
	-- group owner) is always an admin.
	Admins = {
		-- [123456789] = true,
	} :: { [number]: boolean },
	AdminGroup = { GroupId = 0, MinRank = 254 },

	-- Detailed capture (MicroProfiler-style clip)
	Capture = {
		MinSeconds = 30,
		MaxSeconds = 60,
		DefaultSeconds = 45,
		ChunkSeconds = 10, -- upload a chunk every N seconds while recording
		HeavySampleInterval = 0.5, -- memory tags / network / pings sampled this often
		CooldownSeconds = 120,
	},

	-- Cross-server commands from Discord (via Open Cloud MessagingService)
	MessagingTopic = "AnalyticsCommand",
}

return Config
