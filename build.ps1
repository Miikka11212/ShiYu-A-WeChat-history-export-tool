$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONNOUSERSITE = '1'
# Resolve native dependencies only from this venv and Windows. A developer's
# unrelated Poppler/ICU on PATH can otherwise shadow the Windows ICU API.
$taskPythonBase = & '.\.venv\Scripts\python.exe' -c 'import sys; print(sys.base_prefix)'
if ($LASTEXITCODE -ne 0) { throw 'Cannot resolve the Python runtime.' }
$env:PATH = "$PSScriptRoot\.venv\Scripts;$taskPythonBase;$env:SystemRoot\System32;$env:SystemRoot"
$env:PYINSTALLER_CONFIG_DIR = "$PSScriptRoot\artifacts\pyinstaller-cache"
& '.\.venv\Scripts\python.exe' tools\bundle_licenses.py
if ($LASTEXITCODE -ne 0) { throw 'License collection failed.' }
& '.\.venv\Scripts\python.exe' -m PyInstaller --clean --noconfirm --windowed --onedir --workpath artifacts\pyinstaller-work --distpath artifacts\package-build --name Shiyu --collect-all frida --collect-all imageio_ffmpeg --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets --exclude-module PySide6.QtQml --exclude-module PySide6.QtQuick main.py
if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
# The release directory may contain real archives and exports. Build in a
# separate disposable directory and copy program files, preserving user data.
New-Item -ItemType Directory -Path 'dist\Shiyu' -Force | Out-Null
Get-ChildItem -LiteralPath 'artifacts\package-build\Shiyu' | Copy-Item -Destination 'dist\Shiyu' -Recurse -Force
Copy-Item -LiteralPath 'README.md' -Destination 'dist\Shiyu\README.md'
Copy-Item -LiteralPath 'THIRD_PARTY_NOTICES.md' -Destination 'dist\Shiyu\THIRD_PARTY_NOTICES.md'
Copy-Item -LiteralPath 'artifacts\licenses' -Destination 'dist\Shiyu' -Recurse -Force
if (Test-Path -LiteralPath 'local.json') { Copy-Item -LiteralPath 'local.json' -Destination 'dist\Shiyu\local.json' }
Write-Output 'Built dist\Shiyu\Shiyu.exe'
