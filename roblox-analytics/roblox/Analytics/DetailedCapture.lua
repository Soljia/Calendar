--!nonstrict
-- Detailed 30-60 second "clip" of server performance, uploaded to the
-- collector for offline analysis (it converts the clip into a Chrome/Perfetto
-- trace).
--
-- Roblox doesn't let scripts start or export the real MicroProfiler, so this
-- records what scripts *can* see, every frame:
--   * frame time (Heartbeat dt) and the PreSimulation->PostSimulation span
--   * Stats.HeartbeatTimeMs, Stats.PhysicsStepTimeMs, Lua heap
--   * every Analytics.profile() zone (name, start, duration)
--   * script errors, timestamped
-- and every Config.Capture.HeavySampleInterval seconds:
--   * memory by category, network, instance/physics counts, player pings
--
-- The same zones are labelled with debug.profilebegin, so a real MicroProfiler
-- dump taken by hand (see README) shows them too.

local RunService = game:GetService("RunService")
local Players = game:GetService("Players")

local Config = require(script.Parent.Config)
local Util = require(script.Parent.Util)
local Transport = require(script.Parent.Transport)
local ServerStats = require(script.Parent.Collectors.ServerStats)
local PlayerStats = require(script.Parent.Collectors.PlayerStats)
local Metrics = require(script.Parent.Collectors.Metrics)
local Errors = require(script.Parent.Collectors.Errors)

local round = Util.round
local CaptureConfig = Config.Capture
local JOB_ID = if game.JobId ~= "" then game.JobId else "studio"
local MAX_ZONES_PER_CHUNK = 20000

local DetailedCapture = {}

local active = nil
local lastFinishedAt = -math.huge
local lastResult = nil

local function newBuffers()
	return {
		frames = { t = {}, dt = {}, simMs = {}, heartbeatMs = {}, physicsMs = {}, luaMb = {} },
		heavy = {},
		zones = {},
		errors = {},
	}
end

local function postWithRetry(path: string, body: any): boolean
	for attempt = 1, 3 do
		local ok, err = Transport.post(path, body)
		if ok then
			return true
		end
		warn(string.format("[Analytics] capture upload %s failed (%d/3): %s", path, attempt, tostring(err)))
		task.wait(attempt * 2)
	end
	return false
end

local function heavySample(c)
	local pings = {}
	for _, ping in PlayerStats.pings() do
		table.insert(pings, ping)
	end
	table.insert(c.buffers.heavy, {
		t = round(os.clock() - c.startClock, 4),
		memoryMb = round(Util.read(function() return game:GetService("Stats"):GetTotalMemoryUsageMb() end), 1),
		memoryByTag = ServerStats.memoryByTag(),
		network = ServerStats.network(),
		counts = ServerStats.counts(),
		players = #Players:GetPlayers(),
		pingAvgMs = round(Util.mean(pings), 0),
		pingMaxMs = round(Util.percentile(pings, 1), 0),
	})
end

local function uploadChunk(c, final: boolean)
	local buffers = c.buffers
	c.buffers = newBuffers()
	c.seq += 1
	local body = {
		jobId = JOB_ID,
		seq = c.seq,
		final = final,
		frames = buffers.frames,
		heavy = buffers.heavy,
		zones = buffers.zones,
		errors = buffers.errors,
	}
	if final then
		return postWithRetry("/capture/" .. c.id .. "/chunk", body)
	end
	task.spawn(postWithRetry, "/capture/" .. c.id .. "/chunk", body)
	return true
end

local function summarise(c)
	local dts = c.allDt
	local total, worst = 0, {}
	for i, dt in dts do
		total += dt
		table.insert(worst, { i = i, dt = dt })
	end
	table.sort(worst, function(a, b)
		return a.dt > b.dt
	end)
	local worstFrames = {}
	for i = 1, math.min(10, #worst) do
		local w = worst[i]
		table.insert(worstFrames, { t = round(c.allT[w.i], 3), ms = round(w.dt * 1000, 2) })
	end

	local zones = {}
	for name, z in c.zoneTotals do
		table.insert(zones, { name = name, calls = z.calls, totalMs = round(z.total * 1000, 2), maxMs = round(z.max * 1000, 3) })
	end
	table.sort(zones, function(a, b)
		return a.totalMs > b.totalMs
	end)
	while #zones > 15 do
		table.remove(zones)
	end

	local spikes = 0
	for _, dt in dts do
		if dt > 1 / 30 then
			spikes += 1
		end
	end

	return {
		frames = #dts,
		seconds = round(os.clock() - c.startClock, 2),
		fpsAvg = round(#dts > 0 and #dts / total or nil, 1),
		frameP50Ms = round((Util.percentile(dts, 0.5) or 0) * 1000, 2),
		frameP99Ms = round((Util.percentile(dts, 0.99) or 0) * 1000, 2),
		frameSpikes = spikes,
		worstFrames = worstFrames,
		topZones = zones,
		errors = c.errorCount,
		memoryStartMb = c.memoryStartMb,
		memoryEndMb = round(Util.read(function() return game:GetService("Stats"):GetTotalMemoryUsageMb() end), 1),
		chunks = c.seq,
	}
end

local function stopCapture(c)
	for _, conn in c.connections do
		conn:Disconnect()
	end
	Metrics.setZoneSink(nil)
	active = nil
	lastFinishedAt = os.clock()

	local uploaded = uploadChunk(c, true)
	local summary = summarise(c)
	local finished = postWithRetry("/capture/" .. c.id .. "/finish", { jobId = JOB_ID, summary = summary })
	lastResult = { id = c.id, ok = uploaded and finished, summary = summary }
	if c.onFinished then
		task.spawn(c.onFinished, lastResult)
	end
end

-- Starts a capture. Returns (true, captureId) or (false, reason).
function DetailedCapture.start(seconds: number?, requestedBy: string, onFinished: ((any) -> ())?): (boolean, string)
	if active then
		return false, "a capture is already running (" .. active.id .. ")"
	end
	local since = os.clock() - lastFinishedAt
	if since < CaptureConfig.CooldownSeconds then
		return false, string.format("cooldown: try again in %ds", math.ceil(CaptureConfig.CooldownSeconds - since))
	end
	if not Transport.isEnabled() then
		return false, "uploads are disabled (Studio or missing secret)"
	end

	local duration = math.clamp(math.floor(seconds or CaptureConfig.DefaultSeconds), CaptureConfig.MinSeconds, CaptureConfig.MaxSeconds)
	local id = Util.makeId(string.sub(JOB_ID, 1, 8))

	local okStart, err = Transport.post("/capture/" .. id .. "/start", {
		jobId = JOB_ID,
		duration = duration,
		requestedBy = requestedBy,
		snapshot = {
			players = #Players:GetPlayers(),
			quick = ServerStats.quick(),
			memoryByTag = ServerStats.memoryByTag(),
			network = ServerStats.network(),
			counts = ServerStats.counts(),
		},
	})
	if not okStart then
		return false, "collector rejected start: " .. tostring(err)
	end

	local c = {
		id = id,
		duration = duration,
		startClock = os.clock(),
		seq = 0,
		buffers = newBuffers(),
		allDt = {},
		allT = {},
		zoneTotals = {},
		errorCount = 0,
		memoryStartMb = round(Util.read(function() return game:GetService("Stats"):GetTotalMemoryUsageMb() end), 1),
		connections = {},
		onFinished = onFinished,
	}
	active = c

	local preSim, postSim = 0, 0
	local nextHeavy = 0
	local nextChunk = CaptureConfig.ChunkSeconds

	table.insert(c.connections, RunService.PreSimulation:Connect(function()
		preSim = os.clock()
	end))
	table.insert(c.connections, RunService.PostSimulation:Connect(function()
		postSim = os.clock()
	end))
	table.insert(c.connections, RunService.Heartbeat:Connect(function(dt)
		if active ~= c then
			return
		end
		local t = os.clock() - c.startClock
		local quick = ServerStats.quick()
		local f = c.buffers.frames
		table.insert(f.t, round(t, 4))
		table.insert(f.dt, round(dt * 1000, 3))
		table.insert(f.simMs, round(math.max(0, postSim - preSim) * 1000, 3))
		table.insert(f.heartbeatMs, quick.heartbeatMs or 0)
		table.insert(f.physicsMs, quick.physicsStepMs or 0)
		table.insert(f.luaMb, quick.luaHeapMb or 0)
		table.insert(c.allDt, dt)
		table.insert(c.allT, t)

		if t >= nextHeavy then
			nextHeavy = t + CaptureConfig.HeavySampleInterval
			heavySample(c)
		end
		if t >= c.duration then
			task.spawn(stopCapture, c)
		elseif t >= nextChunk then
			nextChunk = t + CaptureConfig.ChunkSeconds
			uploadChunk(c, false)
		end
	end))

	Metrics.setZoneSink(function(name, started, elapsed)
		if active ~= c then
			return
		end
		local z = c.zoneTotals[name]
		if not z then
			z = { calls = 0, total = 0, max = 0 }
			c.zoneTotals[name] = z
		end
		z.calls += 1
		z.total += elapsed
		z.max = math.max(z.max, elapsed)
		if #c.buffers.zones < MAX_ZONES_PER_CHUNK then
			table.insert(c.buffers.zones, { n = name, t = round(started - c.startClock, 6), d = round(elapsed * 1000, 4) })
		end
	end)

	return true, id
end

-- Hook errors once; only recorded while a capture is active.
Errors.onError(function(message, source)
	local c = active
	if c then
		c.errorCount += 1
		table.insert(c.buffers.errors, { t = round(os.clock() - c.startClock, 4), message = Util.truncate(message, 300), source = source })
	end
end)

function DetailedCapture.status()
	if active then
		return {
			running = true,
			id = active.id,
			elapsed = math.floor(os.clock() - active.startClock),
			duration = active.duration,
		}
	end
	return { running = false, last = lastResult }
end

return DetailedCapture
