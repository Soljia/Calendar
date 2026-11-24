--!nonstrict
-- Always-on server engine statistics: frame timing, physics, memory,
-- network, instance counts and DataStore budgets.

local RunService = game:GetService("RunService")
local Stats = game:GetService("Stats")
local Players = game:GetService("Players")
local DataStoreService = game:GetService("DataStoreService")

local Util = require(script.Parent.Parent.Util)
local read, round = Util.read, Util.round

local ServerStats = {}

local frameTimes: { number } = {}
local connection: RBXScriptConnection? = nil
local startClock = os.clock()

local memoryTags = Enum.DeveloperMemoryTag:GetEnumItems()
local budgetTypes = Enum.DataStoreRequestType:GetEnumItems()

function ServerStats.start()
	startClock = os.clock()
	connection = RunService.Heartbeat:Connect(function(dt)
		table.insert(frameTimes, dt)
	end)
end

function ServerStats.stop()
	if connection then
		connection:Disconnect()
		connection = nil
	end
end

-- Per-category memory in MB (Instances, LuaHeap, PhysicsParts, Sounds, ...).
function ServerStats.memoryByTag(): { [string]: number }
	local out = {}
	for _, tag in memoryTags do
		local mb = read(function()
			return Stats:GetMemoryUsageMbForTag(tag)
		end)
		if mb and mb > 0.01 then
			out[tag.Name] = round(mb, 2)
		end
	end
	return out
end

function ServerStats.dataStoreBudgets(): { [string]: number }
	local out = {}
	for _, requestType in budgetTypes do
		local budget = read(function()
			return DataStoreService:GetRequestBudgetForRequestType(requestType)
		end)
		if budget then
			out[requestType.Name] = budget
		end
	end
	return out
end

-- Cheap engine counters, also used by the detailed capture.
function ServerStats.quick()
	return {
		heartbeatMs = round(read(function() return Stats.HeartbeatTimeMs end), 3),
		physicsStepMs = round(read(function() return Stats.PhysicsStepTimeMs end), 3),
		physicsFps = round(read(function() return workspace:GetRealPhysicsFPS() end), 1),
		luaHeapMb = round(gcinfo() / 1024, 2),
	}
end

function ServerStats.network()
	return {
		recvKbps = round(read(function() return Stats.DataReceiveKbps end), 2),
		sendKbps = round(read(function() return Stats.DataSendKbps end), 2),
		physRecvKbps = round(read(function() return Stats.PhysicsReceiveKbps end), 2),
		physSendKbps = round(read(function() return Stats.PhysicsSendKbps end), 2),
	}
end

function ServerStats.counts()
	return {
		instances = read(function() return Stats.InstanceCount end),
		primitives = read(function() return Stats.PrimitivesCount end),
		movingPrimitives = read(function() return Stats.MovingPrimitivesCount end),
		contacts = read(function() return Stats.ContactsCount end),
		awakeParts = read(function() return workspace:GetNumAwakeParts() end),
	}
end

-- Full sample; resets the frame-time window.
function ServerStats.sample()
	local frames = frameTimes
	frameTimes = {}

	local fps, fpsMin, frameP99, frameMax, spikes = nil, nil, nil, nil, 0
	if #frames > 0 then
		local total, maxDt = 0, 0
		for _, dt in frames do
			total += dt
			if dt > maxDt then
				maxDt = dt
			end
			if dt > 1 / 30 then
				spikes += 1 -- frames slower than 30 FPS
			end
		end
		fps = #frames / total
		fpsMin = 1 / maxDt
		frameP99 = (Util.percentile(frames, 0.99) or 0) * 1000
		frameMax = maxDt * 1000
	end

	local quick = ServerStats.quick()
	local counts = ServerStats.counts()

	return {
		uptime = math.floor(os.clock() - startClock),
		players = #Players:GetPlayers(),
		maxPlayers = Players.MaxPlayers,

		fps = round(fps, 1),
		fpsMin = round(fpsMin, 1),
		frameP99Ms = round(frameP99, 2),
		frameMaxMs = round(frameMax, 2),
		frameSpikes = spikes,
		heartbeatMs = quick.heartbeatMs,
		physicsStepMs = quick.physicsStepMs,
		physicsFps = quick.physicsFps,

		memoryMb = round(read(function() return Stats:GetTotalMemoryUsageMb() end), 1),
		luaHeapMb = quick.luaHeapMb,
		memoryByTag = ServerStats.memoryByTag(),

		network = ServerStats.network(),
		counts = counts,
		dataStoreBudget = ServerStats.dataStoreBudgets(),
		streamingEnabled = workspace.StreamingEnabled,
	}
end

return ServerStats
