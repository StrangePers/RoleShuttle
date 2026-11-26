$ErrorActionPreference='Stop'
& (Join-Path $PSScriptRoot 'Start.ps1') -StartOnly
if($LASTEXITCODE){exit $LASTEXITCODE}
$settings=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'desktop.json') -Raw|ConvertFrom-Json
$petProfile=Join-Path $env:LOCALAPPDATA 'AgentConsole-CodeWisp'
$exe=Join-Path $env:LOCALAPPDATA 'Programs/AgentWhisker/AgentWhisker.exe'
$env:AGENTWHISKER_ARCHIVE_PYTHON=$settings.python
$env:AGENTWHISKER_ARCHIVE_LAUNCHER=Join-Path $PSScriptRoot 'desktop.py'
$env:AGENTWHISKER_STATUS_PUBLISHER=Join-Path $PSScriptRoot 'companion-follow.py'
$env:AGENTWHISKER_ROLESHUTTLE_STATUS_FILE=Join-Path $settings.profile 'agentwhisker-status.json'
Remove-Item Env:AGENT_CONSOLE_PET_PYTHON -ErrorAction SilentlyContinue
$arguments = @(('"--user-data-dir='+$petProfile+'"'))
if (!(Test-Path -LiteralPath (Join-Path $petProfile 'settings.json'))) { $arguments += '--provider=roleshuttle' }
Start-Process -FilePath $exe -ArgumentList $arguments -WindowStyle Hidden
