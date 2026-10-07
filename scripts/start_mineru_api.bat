@echo off
REM ============================================================
REM 启动 MinerU 常驻解析服务(推荐)
REM
REM 为什么要常驻:不启动它时,document 解析走的是"每次调用自起临时服务",
REM 模型每张图都要重新加载 —— 实测单张约 115 秒;常驻后模型只加载一次,
REM 实测单张 18~28 秒(CPU)。
REM
REM 项目侧配置(放在 .env 或启动环境里):
REM   OCR_ENGINE=mineru
REM   MINERU_API_URL=http://127.0.0.1:8321
REM 若本服务未启动,引擎会自动退回"不用 --api-url"(慢但能出结论,不会失败)。
REM
REM 依赖:你本机已安装 MinerU,并已设置 MINERU_EXE / MODELSCOPE_CACHE。
REM 可用环境变量覆盖:MINERU_EXE / MODELSCOPE_CACHE / MINERU_MODEL_SOURCE / MINERU_API_PORT
REM ============================================================
setlocal

if "%MINERU_EXE%"=="" (
  echo [错误] 请设置 MINERU_EXE 指向你本机的 mineru-api 可执行文件
  echo        例如 set MINERU_EXE=D:\venvs\mineru\Scripts\mineru-api.exe
  exit /b 1
)
if "%MODELSCOPE_CACHE%"=="" set "MODELSCOPE_CACHE=%USERPROFILE%\.cache\modelscope"
if "%MINERU_MODEL_SOURCE%"=="" set "MINERU_MODEL_SOURCE=modelscope"
if "%MINERU_API_PORT%"=="" set "MINERU_API_PORT=8321"

if not exist "%MINERU_EXE%" (
  echo [错误] 找不到 mineru-api: %MINERU_EXE%
  echo        请先安装 MinerU,或用 MINERU_EXE 指定路径。
  exit /b 1
)

echo 启动 MinerU 常驻服务
echo   可执行文件: %MINERU_EXE%
echo   模型缓存  : %MODELSCOPE_CACHE%
echo   监听      : http://127.0.0.1:%MINERU_API_PORT%
echo   就绪判断  : curl http://127.0.0.1:%MINERU_API_PORT%/docs
echo 按 Ctrl+C 停止。
echo.

set "MODELSCOPE_CACHE=%MODELSCOPE_CACHE%"
set "MINERU_MODEL_SOURCE=%MINERU_MODEL_SOURCE%"
"%MINERU_EXE%" --host 127.0.0.1 --port %MINERU_API_PORT%

endlocal
