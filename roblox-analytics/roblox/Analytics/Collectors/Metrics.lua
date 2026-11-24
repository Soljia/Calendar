--!nonstrict
-- Custom game metrics (counters, gauges, timings), profiled code zones and
-- RemoteEvent traffic. Zones are also forwarded to the detailed capture and
-- labelled with debug.profilebegin so they appear in the real MicroProfiler.

local Util = require(script.Parent.Parent.Util)
local round = Util.round

local Metrics = {}

local counters: { [string]: number } = {}
local gauges: { [string]: number } = {}
local timings: { [string]: { number } } = {}
local remotes: { [string]: { calls: number, bytesApprox: number, callers: { [Player]: number } } } = {}

-- Set by DetailedCapture while recording: fn(name, startClock, durationSeconds)
local zoneSink: ((string, number, number) -> ())? = nil

function Metrics.setZoneSink(fn: ((string, number, number) -> ())?)
	zoneSink = fn
end

function Metrics.increment(name: string, amount: number?)
	counters[name] = (counters[name] or 0) + (amount or 1)
end

function Metrics.gauge(name: string, value: number)
	gauges[name] = value
end

function Metrics.timing(name: string, ms: number)
	local list = timings[name]
	if not list then
		list = {}
		timings[name] = list
	end
	if #list < 2000 then
		table.insert(list, ms)
	end
end

-- Runs fn(...) inside a named zone and records its duration.
-- Use for non-yielding work (per-frame systems, handlers); a zone that yields
-- spans frames and will show as a broken label in the MicroProfiler.
function Metrics.profile<T...>(name: string, fn: (T...) -> ...any, ...: T...): ...any
	debug.profilebegin(name)
	local started = os.clock()
	local results = table.pack(pcall(fn, ...))
	local elapsed = os.clock() - started
	debug.profileend()

	Metrics.timing(name, elapsed * 1000)
	if zoneSink then
		zoneSink(name, started, elapsed)
	end
	if not results[1] then
		error(results[2], 2)
	end
	return table.unpack(results, 2, results.n)
end

-- Wraps a function so every call is profiled under `name`.
function Metrics.wrap(name: string, fn: (...any) -> ...any): (...any) -> ...any
	return function(...)
		return Metrics.profile(name, fn, ...)
	end
end

-- Very rough payload size estimate for remote traffic accounting.
local function approxSize(value: any, depth: number?): number
	local d = depth or 0
	local t = typeof(value)
	if t == "string" then
		return #value
	elseif t == "table" and d < 3 then
		local n = 0
		for k, v in value do
			n += approxSize(k, d + 1) + approxSize(v, d + 1)
		end
		return n
	end
	return 8
end

-- Counts calls (and spammy callers) on a RemoteEvent / RemoteFunction.
-- Call before connecting your own handlers.
function Metrics.trackRemote(remote: Instance)
	local key = remote:GetFullName()
	local entry = { calls = 0, bytesApprox = 0, callers = {} }
	remotes[key] = entry
	local function record(player: Player, ...)
		entry.calls += 1
		entry.callers[player] = (entry.callers[player] or 0) + 1
		local args = table.pack(...)
		for i = 1, args.n do
			entry.bytesApprox += approxSize(args[i])
		end
	end
	if remote:IsA("RemoteEvent") or remote:IsA("UnreliableRemoteEvent") then
		local event: any = remote
		event.OnServerEvent:Connect(record)
	else
		warn("[Analytics] trackRemote: only RemoteEvent/UnreliableRemoteEvent can be tracked passively")
	end
end

function Metrics.sample(intervalSeconds: number)
	local timingSummary = {}
	for name, list in timings do
		timingSummary[name] = {
			count = #list,
			avgMs = round(Util.mean(list), 3),
			p95Ms = round(Util.percentile(list, 0.95), 3),
			maxMs = round(Util.percentile(list, 1), 3),
		}
	end

	local remoteSummary = {}
	for name, r in remotes do
		local topCaller = 0
		for _, n in r.callers do
			topCaller = math.max(topCaller, n)
		end
		if r.calls > 0 then
			remoteSummary[name] = {
				callsPerSec = round(r.calls / intervalSeconds, 2),
				kbps = round(r.bytesApprox / 1024 / intervalSeconds, 2),
				topCallerPerSec = round(topCaller / intervalSeconds, 2),
			}
		end
		r.calls, r.bytesApprox, r.callers = 0, 0, {}
	end

	local result = {
		counters = counters,
		gauges = table.clone(gauges),
		timings = timingSummary,
		remotes = remoteSummary,
	}
	counters, timings = {}, {}
	return result
end

return Metrics
