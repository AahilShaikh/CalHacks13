@echo off
cd /d "%~dp0"
if not exist "ComfyUI\custom_nodes\aida_scan" mkdir "ComfyUI\custom_nodes\aida_scan"
copy /Y "backend\aida\scan\comfy_nodes\__init__.py" "ComfyUI\custom_nodes\aida_scan\__init__.py" >nul
copy /Y "backend\aida\scan\comfy_nodes\board.py" "ComfyUI\custom_nodes\aida_scan\board.py" >nul
copy /Y "backend\aida\scan\comfy_nodes\requirements.txt" "ComfyUI\custom_nodes\aida_scan\requirements.txt" >nul
ComfyUI\.venv\Scripts\python.exe -c "import cv2; assert hasattr(cv2.aruco, 'CharucoDetector')"
if errorlevel 1 (
  echo Install scanner dependencies: uv pip install --python ComfyUI/.venv/Scripts/python.exe -r backend/aida/scan/comfy_nodes/requirements.txt
  exit /b 1
)
echo Starting ComfyUI server for Pixal3D on http://127.0.0.1:8188 ...
ComfyUI\.venv\Scripts\python.exe ComfyUI\main.py --listen 127.0.0.1 --port 8188 --auto-launch
pause
