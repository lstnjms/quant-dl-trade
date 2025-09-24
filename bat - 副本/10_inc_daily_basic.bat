@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul

rem === 通用配置（按需修改）===
set "CONDA_ROOT=D:\anaconda3"
set "CONDA_ENV=quant"
set "WORK_DIR=D:\QS\DL\src"
set "LOG_DIR=D:\QS\DL\logs\daily"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

rem === 时间戳用于日志文件名 ===
for /f "tokens=1 delims=." %%i in ('wmic os get localdatetime ^| find "."') do set "DTS=%%i"
set "TS=!DTS:~0,8!_!DTS:~8,6!"

set "LOG_FILE=%LOG_DIR%\10-inc_daily_basic-!TS!.log"

echo [START] 10-每日指标(增量) %%date%% %%time%% > "%LOG_FILE%"
call "%CONDA_ROOT%\Scripts\activate.bat" "%CONDA_ENV%" >> "%LOG_FILE%" 2>&1
pushd "%WORK_DIR%" >> "%LOG_FILE%" 2>&1

rem === 具体任务 ===
python inc_daily_basic.py --index 000905.SH >> "%LOG_FILE%" 2>&1
set "ERR=!ERRORLEVEL!"

popd >> "%LOG_FILE%" 2>&1
echo [END] code=!ERR! %%date%% %%time%% >> "%LOG_FILE%"
exit /b !ERR!

