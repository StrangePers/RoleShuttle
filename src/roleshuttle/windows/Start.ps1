param([switch]$StartOnly)
$ErrorActionPreference = 'Stop'
$settings = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'desktop.json') -Raw | ConvertFrom-Json
$app = Join-Path $PSScriptRoot 'desktop.py'
$listeners = @(Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $settings.port -State Listen -ErrorAction SilentlyContinue)
if(!$listeners){
    Start-Process -FilePath $settings.python -ArgumentList @('-I','-B',('"' + $app + '"'),'serve') `
      -WorkingDirectory $PSScriptRoot -WindowStyle Hidden `
      -RedirectStandardError (Join-Path $PSScriptRoot 'desktop.stderr.log') `
      -RedirectStandardOutput (Join-Path $PSScriptRoot 'desktop.stdout.log')
}
$action = if($StartOnly){'health'}else{'open'}
& $settings.python -I -B $app $action
exit $LASTEXITCODE
