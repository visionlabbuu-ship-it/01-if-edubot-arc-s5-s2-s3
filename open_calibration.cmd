@echo off
setlocal

set "ROOT=%~dp0"
set "HOST=127.0.0.1"
set "PORT="
set "PYTHON=python"

if exist "%ROOT%.venv\Scripts\python.exe" (
  set "PYTHON=.venv\Scripts\python.exe"
) else (
  where python >nul 2>nul
  if errorlevel 1 (
    where py >nul 2>nul
    if errorlevel 1 (
      echo Python was not found on PATH.
      pause
      exit /b 1
    )
    set "PYTHON=py -3"
  )
)

cd /d "%ROOT%"

for /f %%P in ('powershell -NoProfile -ExecutionPolicy Bypass -Command "$ports = 8787..8797; foreach ($p in $ports) { $state = 'http://%HOST%:' + $p + '/api/state'; $image = 'http://%HOST%:' + $p + '/image/source'; try { $r = Invoke-RestMethod -Uri $state -TimeoutSec 5; if ($r.ok) { $i = Invoke-WebRequest -Uri $image -UseBasicParsing -TimeoutSec 5; $ct = [string]$i.Headers['Content-Type']; if ($i.StatusCode -eq 200 -and $ct -like 'image/*') { Write-Output $p; exit 0 } } } catch {} }; foreach ($p in $ports) { $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Parse('%HOST%'), $p); try { $listener.Start(); $listener.Stop(); Write-Output $p; exit 0 } catch { try { $listener.Stop() } catch {} } }; exit 1"') do if not defined PORT set "PORT=%%P"
if not defined PORT (
  echo No available calibration web port found from 8787 to 8797.
  pause
  exit /b 1
)

call :set_urls

powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r = Invoke-RestMethod -Uri '%STATE_URL%' -TimeoutSec 5; if (-not $r.ok) { exit 1 }; $i = Invoke-WebRequest -Uri '%IMAGE_URL%' -UseBasicParsing -TimeoutSec 5; $ct = [string]$i.Headers['Content-Type']; if ($i.StatusCode -eq 200 -and $ct -like 'image/*') { exit 0 } exit 1 } catch { exit 1 }" >nul 2>nul
if errorlevel 1 (
  echo Starting calibration server on %URL%
  start "ARC Calibration Server" /min cmd /c "cd /d ""%ROOT%"" && %PYTHON% -m tools.calibration.web --host %HOST% --port %PORT%"

  powershell -NoProfile -ExecutionPolicy Bypass -Command "$deadline = (Get-Date).AddSeconds(20); while ((Get-Date) -lt $deadline) { try { $r = Invoke-RestMethod -Uri '%STATE_URL%' -TimeoutSec 5; if ($r.ok) { $i = Invoke-WebRequest -Uri '%IMAGE_URL%' -UseBasicParsing -TimeoutSec 5; if ($i.StatusCode -eq 200 -and $i.Headers['Content-Type'] -like 'image/*') { exit 0 } } } catch {} Start-Sleep -Milliseconds 500 } exit 1" >nul 2>nul
  if errorlevel 1 (
    echo Calibration server did not respond with a valid image at %IMAGE_URL%.
    pause
    exit /b 1
  )
) else (
  echo Calibration server is already running and serving images.
)

echo Opening %URL%
start "" "%URL%"
exit /b 0

:set_urls
set "URL=http://%HOST%:%PORT%/"
set "STATE_URL=http://%HOST%:%PORT%/api/state"
set "IMAGE_URL=http://%HOST%:%PORT%/image/source"
exit /b 0
