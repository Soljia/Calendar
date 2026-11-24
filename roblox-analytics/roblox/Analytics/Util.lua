--!strict
-- Small helpers shared by the analytics modules.

local Util = {}

-- Reads a value, returning nil instead of erroring (some Stats members are
-- restricted or may not exist on every engine version).
function Util.read<T>(fn: () -> T): T?
	local ok, value = pcall(fn)
	if ok then
		return value
	end
	return nil
end

function Util.round(n: number?, places: number?): number?
	if n == nil or n ~= n then -- nil or NaN
		return nil
	end
	local m = 10 ^ (places or 2)
	return math.floor(n * m + 0.5) / m
end

-- Percentile of an array of numbers (copies + sorts).
function Util.percentile(values: { number }, p: number): number?
	local n = #values
	if n == 0 then
		return nil
	end
	local sorted = table.clone(values)
	table.sort(sorted)
	local idx = math.clamp(math.ceil(p * n), 1, n)
	return sorted[idx]
end

function Util.mean(values: { number }): number?
	local n = #values
	if n == 0 then
		return nil
	end
	local sum = 0
	for _, v in values do
		sum += v
	end
	return sum / n
end

function Util.truncate(s: string, max: number): string
	if #s <= max then
		return s
	end
	return string.sub(s, 1, max - 3) .. "..."
end

-- Unique-enough id for captures: <jobId prefix>-<unix>-<random>.
function Util.makeId(prefix: string): string
	return string.format("%s-%d-%04x", prefix, os.time(), math.random(0, 0xFFFF))
end

return Util
