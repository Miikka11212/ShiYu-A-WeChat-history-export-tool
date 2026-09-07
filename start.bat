@echo off
cd /d "%~dp0"
if exist "dist\Shiyu\Shiyu.exe" (
    if not exist "dist\Shiyu\_internal\python312.dll" (
        echo Incomplete app package. Run build.ps1 again and keep the entire dist\Shiyu folder.
        pause
        exit /b 1
    )
    start "" "dist\Shiyu\Shiyu.exe"
) else if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" "main.py"
) else (
    echo Please run setup.ps1 first.
    pause
)
