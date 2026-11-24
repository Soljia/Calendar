--!nonstrict
-- Player/session statistics: joins, leaves, session length, server-measured
-- ping, and client-reported FPS / memory / platform.

local Players = game:GetService("Players")

local Config = require(script.Parent.Parent.Config)
local Util = require(script.Parent.Parent.Util)
local round = Util.round

local PlayerStats = {}

type ClientReport = {
	fps: number,
	memoryMb: number,
	platform: string,
	quality: number?,
	at: number,
}

local joinedAt: { [Player]: number } = {}
local reports: { [Player]: ClientReport } = {}
local lastReportAt: { [Player]: number } = {}
local window = { joins = 0, leaves = 0, sessionSeconds = {} :: { number } }
local clientErrors: { [string]: number } = {}

local VALID_PLATFORMS = { Desktop = true, Mobile = true, Console = true, VR = true, Unknown = true }

local function onJoin(player: Player)
	joinedAt[player] = os.clock()
	window.joins += 1
end

local function onLeave(player: Player)
	local started = joinedAt[player]
	if started then
		table.insert(window.sessionSeconds, os.clock() - started)
	end
	window.leaves += 1
	joinedAt[player] = nil
	reports[player] = nil
	lastReportAt[player] = nil
end

function PlayerStats.start()
	Players.PlayerAdded:Connect(onJoin)
	Players.PlayerRemoving:Connect(onLeave)
	for _, p in Players:GetPlayers() do
		onJoin(p)
	end
end

-- Validates and stores a report from ClientReporter. Clients are untrusted:
-- clamp everything and ignore reports that arrive too often.
function PlayerStats.handleClientReport(player: Player, payload: any)
	local now = os.clock()
	local last = lastReportAt[player]
	if last and now - last < Config.ClientReportInterval * 0.5 then
		return
	end
	lastReportAt[player] = now
	if type(payload) ~= "table" then
		return
	end

	local fps = tonumber(payload.fps)
	local mem = tonumber(payload.memoryMb)
	if not fps or not mem then
		return
	end
	local platform = if type(payload.platform) == "string" and VALID_PLATFORMS[payload.platform]
		then payload.platform
		else "Unknown"

	reports[player] = {
		fps = math.clamp(fps, 0, 1000),
		memoryMb = math.clamp(mem, 0, 64000),
		platform = platform,
		quality = tonumber(payload.quality),
		at = now,
	}

	if type(payload.errors) == "table" then
		local n = 0
		for _, message in payload.errors do
			n += 1
			if n > 5 then
				break
			end
			if type(message) == "string" then
				local key = Util.truncate(message, 200)
				clientErrors[key] = (clientErrors[key] or 0) + 1
			end
		end
	end
end

-- Server-measured round-trip pings in ms.
function PlayerStats.pings(): { [Player]: number }
	local out = {}
	for _, p in Players:GetPlayers() do
		local ok, ping = pcall(p.GetNetworkPing, p)
		if ok and ping then
			out[p] = ping * 1000
		end
	end
	return out
end

function PlayerStats.sample()
	local pingList, fpsList, memList = {}, {}, {}
	local platforms: { [string]: number } = {}
	local perPlayer = {}
	local lowFps = 0
	local staleAfter = Config.ClientReportInterval * 3

	local pings = PlayerStats.pings()
	for _, p in Players:GetPlayers() do
		local ping = pings[p]
		if ping then
			table.insert(pingList, ping)
		end
		local r = reports[p]
		if r and os.clock() - r.at < staleAfter then
			table.insert(fpsList, r.fps)
			table.insert(memList, r.memoryMb)
			platforms[r.platform] = (platforms[r.platform] or 0) + 1
			if r.fps < 30 then
				lowFps += 1
			end
		end
		if Config.IncludeUserIds then
			table.insert(perPlayer, {
				userId = p.UserId,
				pingMs = round(ping, 0),
				fps = r and round(r.fps, 0),
				memoryMb = r and round(r.memoryMb, 0),
				platform = r and r.platform,
				sessionSeconds = joinedAt[p] and math.floor(os.clock() - joinedAt[p]),
			})
		end
	end

	local topErrors = {}
	for message, count in clientErrors do
		table.insert(topErrors, { message = message, count = count })
	end
	table.sort(topErrors, function(a, b)
		return a.count > b.count
	end)
	while #topErrors > 10 do
		table.remove(topErrors)
	end

	local result = {
		count = #Players:GetPlayers(),
		joins = window.joins,
		leaves = window.leaves,
		avgSessionSeconds = round(Util.mean(window.sessionSeconds), 0),
		pingAvgMs = round(Util.mean(pingList), 0),
		pingP95Ms = round(Util.percentile(pingList, 0.95), 0),
		clientFpsAvg = round(Util.mean(fpsList), 1),
		clientFpsP5 = round(Util.percentile(fpsList, 0.05), 1),
		clientMemoryAvgMb = round(Util.mean(memList), 0),
		lowFpsPlayers = lowFps,
		platforms = platforms,
		clientErrors = topErrors,
		players = if Config.IncludeUserIds then perPlayer else nil,
	}

	window = { joins = 0, leaves = 0, sessionSeconds = {} }
	clientErrors = {}
	return result
end

return PlayerStats
