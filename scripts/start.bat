@echo off
REM 一键启动脚本(Windows / PRD §17.2 交付物)
REM 用法: scripts\start.bat            API 8000,工作台 8500
REM       set API_PORT=8102 && set WEB_PORT=8502 && scripts\start.bat   (自定义端口)
setlocal

cd /d "%~dp0\.."

if "%API_PORT%"=="" set API_PORT=8000
if "%WEB_PORT%"=="" set WEB_PORT=8500

if not exist ".venv" (
  echo [start] 创建虚拟环境 .venv
  python -m venv .venv
)

set VENV_PY=.venv\Scripts\python.exe
echo [start] 安装依赖(requirements.txt)
"%VENV_PY%" -m pip install --upgrade pip >nul
"%VENV_PY%" -m pip install -r requirements.txt || goto :error

if not exist ".env" if exist ".env.example" (
  echo [start] 未发现 .env,从 .env.example 复制
  copy /y ".env.example" ".env" >nul
)

if not exist "logs" mkdir logs

echo [start] 启动 API http://127.0.0.1:%API_PORT%/docs
start "docaudit-api" /min "%VENV_PY%" -m uvicorn app.api.main:app --host 127.0.0.1 --port %API_PORT%

echo [start] 启动审核工作台 http://127.0.0.1:%WEB_PORT%
start "docaudit-web" /min "%VENV_PY%" -m streamlit run streamlit_app.py --server.port %WEB_PORT% --server.address 127.0.0.1 --server.headless true

echo [start] 完成。API 文档 http://127.0.0.1:%API_PORT%/docs,工作台 http://127.0.0.1:%WEB_PORT%
goto :eof

:error
echo [start] 依赖安装失败,请检查网络或 Python 版本(建议 3.11/3.12)
exit /b 1
