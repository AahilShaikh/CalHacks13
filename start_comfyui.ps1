Set-Location $PSScriptRoot
$nodeSource = Join-Path $PSScriptRoot 'backend/aida/scan/comfy_nodes'
$nodeTarget = Join-Path $PSScriptRoot 'ComfyUI/custom_nodes/aida_scan'
New-Item -ItemType Directory -Path $nodeTarget -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $nodeSource '__init__.py'), (Join-Path $nodeSource 'board.py'), (Join-Path $nodeSource 'requirements.txt') -Destination $nodeTarget -Force
& '.\ComfyUI\.venv\Scripts\python.exe' -c "import cv2; assert hasattr(cv2.aruco, 'CharucoDetector')"
if ($LASTEXITCODE -ne 0) { throw 'Install scanner dependencies: uv pip install --python ComfyUI/.venv/Scripts/python.exe -r backend/aida/scan/comfy_nodes/requirements.txt' }
Write-Host "Starting ComfyUI server for Pixal3D on http://127.0.0.1:8188 ..." -ForegroundColor Cyan
& ".\ComfyUI\.venv\Scripts\python.exe" ComfyUI\main.py --listen 127.0.0.1 --port 8188 --auto-launch
