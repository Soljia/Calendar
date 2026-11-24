--!nonstrict
-- Server script errors and warnings, de-duplicated by message.

local ScriptContext = game:GetService("ScriptContext")
local LogService = game:GetService("LogService")

local Util = require(script.Parent.Parent.Util)

local Errors = {}

local errors: { [string]: { count: number, source: string, trace: string } } = {}
local errorCount, warningCount = 0, 0
local listeners: { (message: string, source: string) -> () } = {}

-- Strip per-instance noise (numbers, quoted names) so similar errors group.
local function normalise(message: string): string
	return (Util.truncate(message, 300):gsub("%d+", "N"))
end

function Errors.start()
	ScriptContext.Error:Connect(function(message, trace, scriptInstance)
		errorCount += 1
		local key = normalise(message)
		local entry = errors[key]
		if entry then
			entry.count += 1
		else
			errors[key] = {
				count = 1,
				source = if scriptInstance then scriptInstance:GetFullName() else "?",
				trace = Util.truncate(trace or "", 500),
			}
		end
		for _, fn in listeners do
			task.spawn(fn, message, if scriptInstance then scriptInstance:GetFullName() else "?")
		end
	end)

	LogService.MessageOut:Connect(function(_message, messageType)
		if messageType == Enum.MessageType.MessageWarning then
			warningCount += 1
		end
	end)
end

-- Used by the detailed capture to timestamp errors.
function Errors.onError(fn: (message: string, source: string) -> ())
	table.insert(listeners, fn)
end

function Errors.sample()
	local top = {}
	for message, e in errors do
		table.insert(top, { message = message, count = e.count, source = e.source, trace = e.trace })
	end
	table.sort(top, function(a, b)
		return a.count > b.count
	end)
	while #top > 10 do
		table.remove(top)
	end

	local result = { errors = errorCount, warnings = warningCount, top = top }
	errors, errorCount, warningCount = {}, 0, 0
	return result
end

return Errors
