--!nonstrict
--[[
	Analytics: server performance monitoring for Roblox.

	Usage (from a server Script):
		local Analytics = require(game.ServerScriptService.Analytics)
		Analytics.start()

		-- optional instrumentation in your own code
		Analytics.increment("RoundsStarted")
		Analytics.gauge("ActiveNPCs", #npcs)
		Analytics.profile("NPC.Update", updateNpcs, dt)
		Analytics.trackRemote(ReplicatedStorage.Remotes.Shoot)
		Analytics.event("BossDefeated", { seconds = 312 })

	Admin commands (chat): /capture [30-60]   /perf
	Discord: /capture <jobId> [seconds] (via Open Cloud MessagingService)
]]

local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local HttpService = game:GetService("HttpService")
local MessagingService = game:GetService("MessagingService")
local TextChatService = game:GetService("TextChatService")
local RunService = game:GetService("RunService")

local Config = require(script.Config)
local Util = require(script.Util)
local Transport = require(script.Transport)
local ServerStats = require(script.Collectors.ServerStats)
local PlayerStats = require(script.Collectors.PlayerStats)
local Errors = require(script.Collectors.Errors)
local Metrics = require(script.Collectors.Metrics)
local DetailedCapture = require(script.DetailedCapture)

local Analytics = {}

local started = false
local adminCache: { [Player]: boolean } = {}
local noticeRemote: RemoteEvent

-- Public instrumentation API ---------------------------------------------
Analytics.increment = Metrics.increment
Analytics.gauge = Metrics.gauge
Analytics.timing = Metrics.timing
Analytics.profile = Metrics.profile
Analytics.wrap = Metrics.wrap
Analytics.trackRemote = Metrics.trackRemote

function Analytics.event(name: string, fields: { [string]: any }?)
	Transport.enqueue("event", { name = name, fields = fields })
end

function Analytics.captureStatus()
	return DetailedCapture.status()
end

-- Admins --------------------------------------------------------------------
function Analytics.isAdmin(player: Player): boolean
	local cached = adminCache[player]
	if cached ~= nil then
		return cached
	end
	local isAdmin = Config.Admins[player.UserId] == true
	if not isAdmin and game.CreatorType == Enum.CreatorType.User then
		isAdmin = player.UserId == game.CreatorId
	end
	if not isAdmin and game.CreatorType == Enum.CreatorType.Group then
		local ok, rank = pcall(player.GetRankInGroup, player, game.CreatorId)
		isAdmin = ok and rank == 255
	end
	if not isAdmin and Config.AdminGroup.GroupId ~= 0 then
		local ok, rank = pcall(player.GetRankInGroup, player, Config.AdminGroup.GroupId)
		isAdmin = ok and rank >= Config.AdminGroup.MinRank
	end
	if RunService:IsStudio() then
		isAdmin = true
	end
	adminCache[player] = isAdmin
	return isAdmin
end

local function notify(player: Player?, text: string)
	print("[Analytics] " .. text)
	if player and noticeRemote then
		noticeRemote:FireClient(player, text)
	end
end

-- Detailed capture ------------------------------------------------------
function Analytics.capture(seconds: number?, requestedBy: string, notifyPlayer: Player?): (boolean, string)
	local ok, result = DetailedCapture.start(seconds, requestedBy, function(r)
		if r.ok then
			notify(notifyPlayer, string.format(
				"Capture %s uploaded: %d frames, avg %.1f FPS, p99 %.1f ms, %d spikes. Check Discord #perf-captures.",
				r.id, r.summary.frames, r.summary.fpsAvg or 0, r.summary.frameP99Ms or 0, r.summary.frameSpikes))
		else
			notify(notifyPlayer, "Capture " .. r.id .. " finished but upload failed; see server output.")
		end
	end)
	if ok then
		notify(notifyPlayer, "Recording detailed capture " .. result .. "...")
	else
		notify(notifyPlayer, "Capture not started: " .. result)
	end
	return ok, result
end

local function handleCommand(player: Player, text: string)
	local command, arg = string.match(text, "^(/%a+)%s*(%S*)")
	if command ~= "/capture" and command ~= "/perf" then
		return
	end
	if not Analytics.isAdmin(player) then
		notify(player, "You are not allowed to use " .. command)
		return
	end
	if command == "/capture" then
		local requestedBy = string.format("%s (%d) in-game", player.Name, player.UserId)
		Analytics.capture(tonumber(arg), requestedBy, player)
	else
		local q = ServerStats.quick()
		local status = DetailedCapture.status()
		notify(player, string.format(
			"Server %s | players %d | heartbeat %.2f ms | physics %.2f ms | Lua heap %.1f MB | capture: %s",
			string.sub(game.JobId, 1, 8), #Players:GetPlayers(), q.heartbeatMs or 0, q.physicsStepMs or 0,
			q.luaHeapMb or 0, if status.running then ("recording " .. status.elapsed .. "s") else "idle"))
	end
end

local function setupChatCommands()
	if TextChatService.ChatVersion == Enum.ChatVersion.TextChatService then
		for _, alias in { "/capture", "/perf" } do
			local cmd = Instance.new("TextChatCommand")
			cmd.Name = "Analytics" .. alias:sub(2)
			cmd.PrimaryAlias = alias
			cmd.Parent = TextChatService
			cmd.Triggered:Connect(function(source: TextSource, text: string)
				local player = Players:GetPlayerByUserId(source.UserId)
				if player then
					handleCommand(player, text)
				end
			end)
		end
	else
		local function hook(player: Player)
			player.Chatted:Connect(function(text)
				handleCommand(player, text)
			end)
		end
		Players.PlayerAdded:Connect(hook)
		for _, p in Players:GetPlayers() do
			hook(p)
		end
	end
end

-- Remote commands from Discord: the collector publishes JSON to the topic via
-- Open Cloud. {"action":"capture","jobId":"<id>","seconds":45,"requestedBy":"..."}
local function setupMessaging()
	local ok, err = pcall(function()
		MessagingService:SubscribeAsync(Config.MessagingTopic, function(message)
			local okDecode, data = pcall(HttpService.JSONDecode, HttpService, message.Data)
			if not okDecode or type(data) ~= "table" then
				return
			end
			if data.jobId ~= game.JobId and data.jobId ~= "*" then
				return
			end
			if data.action == "capture" then
				local by = "Discord: " .. Util.truncate(tostring(data.requestedBy or "?"), 64)
				local okCapture, result = Analytics.capture(tonumber(data.seconds), by, nil)
				Transport.enqueue("event", {
					name = "CaptureRequest",
					fields = { ok = okCapture, result = result, requestedBy = by },
				})
				task.spawn(Transport.flush)
			elseif data.action == "flush" then
				task.spawn(Transport.flush)
			end
		end)
	end)
	if not ok then
		warn("[Analytics] MessagingService subscribe failed:", err)
	end
end

-- Lifecycle -----------------------------------------------------------------
local function takeSample()
	Transport.enqueue("sample", {
		server = ServerStats.sample(),
		players = PlayerStats.sample(),
		errors = Errors.sample(),
		metrics = Metrics.sample(Config.SampleInterval),
		transport = Transport.getStats(),
	})
end

function Analytics.start()
	if started then
		return Analytics
	end
	started = true

	Transport.init({
		jobId = if game.JobId ~= "" then game.JobId else "studio",
		placeId = game.PlaceId,
		universeId = game.GameId,
		placeVersion = game.PlaceVersion,
		privateServerId = if game.PrivateServerId ~= "" then game.PrivateServerId else nil,
		reserved = game.PrivateServerId ~= "" and game.PrivateServerOwnerId == 0,
		startedAt = os.time(),
	})

	ServerStats.start()
	PlayerStats.start()
	Errors.start()

	-- remotes for client reports and admin notices
	local folder = Instance.new("Folder")
	folder.Name = "AnalyticsRemotes"
	local reportRemote = Instance.new("RemoteEvent")
	reportRemote.Name = "ClientReport"
	reportRemote.Parent = folder
	noticeRemote = Instance.new("RemoteEvent")
	noticeRemote.Name = "Notice"
	noticeRemote.Parent = folder
	folder.Parent = ReplicatedStorage
	reportRemote.OnServerEvent:Connect(PlayerStats.handleClientReport)

	Players.PlayerRemoving:Connect(function(p)
		adminCache[p] = nil
	end)

	setupChatCommands()
	task.spawn(setupMessaging)

	Transport.enqueue("lifecycle", { event = "start" })
	task.spawn(Transport.flush)

	task.spawn(function()
		while true do
			task.wait(Config.SampleInterval)
			local ok, err = pcall(takeSample)
			if not ok then
				warn("[Analytics] sample failed:", err)
			end
		end
	end)

	task.spawn(function()
		while true do
			task.wait(Config.FlushInterval)
			Transport.flush()
		end
	end)

	game:BindToClose(function()
		pcall(takeSample)
		Transport.enqueue("lifecycle", { event = "shutdown" })
		Transport.flush()
	end)

	return Analytics
end

return Analytics
