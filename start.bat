@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
  echo [ERROR] Python environment not found. See README.md.
  pause
  exit /b 1
)

if not exist "models\faster-whisper-large-v3-turbo\model.bin" (
  echo [ERROR] Speech recognition model not found. See README.md.
  pause
  exit /b 1
)

if not exist "models\llm\Qwen3.5-9B-Q4_K_M.gguf" if not exist "models\llm\Qwen2.5-7B-Instruct-Q4_K_M.gguf" (
  echo [ERROR] Language model not found. See README.md.
  pause
  exit /b 1
)

start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0tray.py"
exit /b 0
