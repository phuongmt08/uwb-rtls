@echo off
setlocal

set VENV_DIR=.venv
set REQ_FILE=requirements.txt

if not exist "%REQ_FILE%" (
  echo [ERROR] Cannot find %REQ_FILE% in the current directory.
  exit /b 1
)

echo [INFO] Recreating virtual environment with Python 3.12...
if exist "%VENV_DIR%" rmdir /s /q "%VENV_DIR%"

set PY312_CMD=py -3.12
%PY312_CMD% -m venv "%VENV_DIR%"
if errorlevel 1 (
  if exist "%USERPROFILE%\AppData\Roaming\uv\python\cpython-3.12.12-windows-x86_64-none\python.exe" (
    "%USERPROFILE%\AppData\Roaming\uv\python\cpython-3.12.12-windows-x86_64-none\python.exe" -m venv "%VENV_DIR%"
  )
)
if errorlevel 1 (
  echo [ERROR] Python 3.12 not found. Install it first, then run this script again.
  exit /b 1
)

echo [INFO] Installing modules from %REQ_FILE%...
"%VENV_DIR%\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 exit /b 1
"%VENV_DIR%\Scripts\python.exe" -m pip install -r "%REQ_FILE%"
if errorlevel 1 exit /b 1

echo [DONE] Installation completed.
echo [INFO] Activate venv in CMD: %VENV_DIR%\Scripts\activate.bat
echo [INFO] Activate venv in PowerShell: .\%VENV_DIR%\Scripts\Activate.ps1
echo [INFO] Activate venv in Git Bash: source %VENV_DIR%/Scripts/activate
exit /b 0
