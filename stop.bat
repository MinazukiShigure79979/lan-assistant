@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" "%~dp0stop_app.py"
) else (
  echo [WARN] Python environment not found; skipping API shutdown.
)
powershell -NoProfile -ExecutionPolicy Bypass -Command "$root = (Get-Location).Path; Start-Sleep -Seconds 2; Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'pythonw.exe' -and $_.CommandLine -like '*subtitle_overlay.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'pythonw.exe' -and $_.CommandLine -like '*voice_overlay.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'pythonw.exe' -and $_.CommandLine -like '*tts_server.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; $local = Get-Process -Name ollama,llama-server -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($root + '\tools\ollama', [System.StringComparison]::OrdinalIgnoreCase) }; if ($local) { $local | Stop-Process -Force; Write-Host 'Local Ollama stopped; VRAM released.' } else { Write-Host 'Local Ollama is not running.' }"
