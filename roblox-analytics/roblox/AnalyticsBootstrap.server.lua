-- Script in ServerScriptService. Starts always-on analytics.
local ServerScriptService = game:GetService("ServerScriptService")

local Analytics = require(ServerScriptService:WaitForChild("Analytics"))
Analytics.start()
