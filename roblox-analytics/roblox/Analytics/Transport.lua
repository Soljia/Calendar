--!nonstrict
-- Batching HTTP transport to the collector.
--  * Records are queued and flushed every Config.FlushInterval seconds.
--  * A token bucket keeps us well under the per-server HttpService limit.
--  * Failed batches are re-queued (bounded by Config.MaxQueue).

local HttpService = game:GetService("HttpService")
local RunService = game:GetService("RunService")

local Config = require(script.Parent.Config)

local Transport = {}

local queue: { any } = {}
local serverMeta: { [string]: any } = {}
local authHeader: any = nil -- string or Secret
local enabled = false
local droppedRecords = 0
local stats = { sent = 0, failed = 0, bytes = 0 }

-- token bucket
local tokens = Config.MaxRequestsPerMinute
local lastRefill = os.clock()

local function takeToken(): boolean
	local now = os.clock()
	tokens = math.min(Config.MaxRequestsPerMinute, tokens + (now - lastRefill) * Config.MaxRequestsPerMinute / 60)
	lastRefill = now
	if tokens >= 1 then
		tokens -= 1
		return true
	end
	return false
end

local function resolveAuth()
	if RunService:IsStudio() then
		authHeader = "Bearer " .. Config.StudioKey
		return
	end
	local ok, secret = pcall(function()
		return HttpService:GetSecret(Config.SecretName)
	end)
	if ok and secret then
		authHeader = (secret :: any):AddPrefix("Bearer ")
	else
		warn("[Analytics] Secret '" .. Config.SecretName .. "' not found; uploads disabled")
		enabled = false
	end
end

function Transport.init(meta: { [string]: any })
	serverMeta = meta
	enabled = (not RunService:IsStudio()) or Config.EnableInStudio
	if enabled then
		resolveAuth()
	end
end

function Transport.isEnabled(): boolean
	return enabled
end

function Transport.getStats()
	return { sent = stats.sent, failed = stats.failed, bytes = stats.bytes, queued = #queue, dropped = droppedRecords }
end

-- Synchronous POST. Returns success, decoded body (or error string).
function Transport.post(path: string, body: any): (boolean, any)
	if not enabled then
		return false, "disabled"
	end
	if not takeToken() then
		return false, "rate-limited"
	end
	local json = HttpService:JSONEncode(body)
	local ok, response = pcall(function()
		return HttpService:RequestAsync({
			Url = Config.Endpoint .. path,
			Method = "POST",
			Headers = {
				["Content-Type"] = "application/json",
				["Authorization"] = authHeader,
			},
			Body = json,
		})
	end)
	if not ok then
		stats.failed += 1
		return false, response
	end
	if not (response :: any).Success then
		stats.failed += 1
		return false, string.format("HTTP %d %s", response.StatusCode, response.StatusMessage)
	end
	stats.sent += 1
	stats.bytes += #json
	local decoded = nil
	if response.Body and #response.Body > 0 then
		local okDecode, value = pcall(HttpService.JSONDecode, HttpService, response.Body)
		decoded = okDecode and value or nil
	end
	return true, decoded
end

function Transport.enqueue(kind: string, data: any)
	table.insert(queue, { kind = kind, t = os.time(), data = data })
	if #queue > Config.MaxQueue then
		table.remove(queue, 1)
		droppedRecords += 1
	end
end

function Transport.flush(): boolean
	if not enabled or #queue == 0 then
		return true
	end
	local batch = queue
	queue = {}
	local ok, err = Transport.post("/ingest", { server = serverMeta, records = batch })
	if not ok then
		-- put records back in front of anything queued meanwhile
		for i = #batch, 1, -1 do
			table.insert(queue, 1, batch[i])
		end
		while #queue > Config.MaxQueue do
			table.remove(queue, 1)
			droppedRecords += 1
		end
		warn("[Analytics] flush failed:", err)
	end
	return ok
end

return Transport
