@echo off
setlocal EnableExtensions EnableDelayedExpansion

rem --- 基础路径 ---
set "CONDA_BAT=D:\anaconda3\condabin\conda.bat"
set "CONDA_ENV=quant"
set "SRC=D:\QS\DL\src"
set "LOGDIR=D:\QS\DL\logs\daily"

if not exist "%LOGDIR%" mkdir "%LOGDIR%"

rem --- 时间戳与日志文件 ---
for /f "tokens=1-4 delims=/:. " %%a in ("%date% %time%") do set "TS=%%a%%b%%c_%%d"
set "LOG=%LOGDIR%\01-full_stock_basic-%TS%.log"

echo ===== 开始运行 ===== >>"%LOG%" 2>&1

rem --- 激活环境 ---
call "%CONDA_BAT%" activate "%CONDA_ENV%" >>"%LOG%" 2>&1
if errorlevel 1 (
  echo [ERR] conda activate 失败 >>"%LOG%" 
  exit /b 9009
)

rem --- 执行 python ---
pushd "%SRC%"
python full_stock_basic.py >>"%LOG%" 2>&1
set "ERR=%errorlevel%"
popd

echo ExitCode=%ERR%
exit /b %ERR%

