-- LocalScript in StarterPlayer > StarterPlayerScripts.
-- Reports client FPS, client memory, platform and client errors to the server,
-- and shows admin notices from the analytics system in chat.

local ReplicatedStorage = game:GetService("ReplicatedStorage")
local RunService = game:GetService("RunService")
local Stats = game:GetService("Stats")
local UserInputService = game:GetService("UserInputService")
local GuiService = game:GetService("GuiService")
local ScriptContext = game:GetService("ScriptContext")
local TextChatService = game:GetService("TextChatService")
local StarterGui = game:GetService("StarterGui")

local REPORT_INTERVAL = 10 -- keep in sync with Config.ClientReportInterval

local remotes = ReplicatedStorage:WaitForChild("AnalyticsRemotes", 30)
if not remotes then
	return
end
local reportRemote = remotes:WaitForChild("ClientReport")
local noticeRemote = remotes:WaitForChild("Notice")

local function platform(): string
	if UserInputService.VREnabled then
		return "VR"
	elseif GuiService:IsTenFootInterface() then
		return "Console"
	elseif UserInputService.TouchEnabled and not UserInputService.KeyboardEnabled then
		return "Mobile"
	elseif UserInputService.KeyboardEnabled then
		return "Desktop"
	end
	return "Unknown"
end

local frames, elapsed = 0, 0
RunService.RenderStepped:Connect(function(dt)
	frames += 1
	elapsed += dt
end)

local pendingErrors = {}
ScriptContext.Error:Connect(function(message)
	if #pendingErrors < 5 then
		table.insert(pendingErrors, string.sub(message, 1, 200))
	end
end)

task.spawn(function()
	while true do
		task.wait(REPORT_INTERVAL)
		local fps = if elapsed > 0 then frames / elapsed else 0
		frames, elapsed = 0, 0
		local okQuality, quality = pcall(function()
			return UserSettings().GameSettings.SavedQualityLevel.Value
		end)
		reportRemote:FireServer({
			fps = fps,
			memoryMb = Stats:GetTotalMemoryUsageMb(),
			platform = platform(),
			quality = if okQuality then quality else nil,
			errors = pendingErrors,
		})
		pendingErrors = {}
	end
end)

noticeRemote.OnClientEvent:Connect(function(text: string)
	if TextChatService.ChatVersion == Enum.ChatVersion.TextChatService then
		local channels = TextChatService:FindFirstChild("TextChannels")
		local system = channels and channels:FindFirstChild("RBXSystem")
		if system then
			system:DisplaySystemMessage("[Analytics] " .. text)
			return
		end
	end
	pcall(StarterGui.SetCore, StarterGui, "ChatMakeSystemMessage", { Text = "[Analytics] " .. text })
end)
