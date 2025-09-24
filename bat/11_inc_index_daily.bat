@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul

rem === 可配参数 ===
set "CONDA_BAT=D:\anaconda3\condabin\conda.bat"
set "CONDA_ENV=quant"
set "SRC=D:\QS\DL\src"
set "LOGDIR=D:\QS\DL\logs\daily"

if not exist "%LOGDIR%" mkdir "%LOGDIR%"

rem === 时间戳：优先 PowerShell，失败用 DATE/TIME 兜底 ===
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "TS=%%i"
if not defined TS (
  set "TS=%DATE:~-4%%DATE:~5,2%%DATE:~8,2%_%TIME:~0,2%%TIME:~3,2%%TIME:~6,2%"
  set "TS=%TS: =0%"
)

set "LOG=%LOGDIR%\11-inc_index_daily-!TS!.log"
echo [START] 11-inc_index_daily %date% %time% > "%LOG%" 2>&1

call "%CONDA_BAT%" activate "%CONDA_ENV%" >>"%LOG%" 2>&1 || exit /b 9009
pushd "%SRC%" >>"%LOG%" 2>&1 || exit /b 2

rem === 关键：inc_index_daily.py 需要 --code 参数（示例同时跑 905/300）===
python inc_index_daily.py --code 000905.SH,000300.SH >>"%LOG%" 2>&1
set "ERR=!ERRORLEVEL!"

popd >>"%LOG%" 2>&1
echo ExitCode=!ERR!>>"%LOG%"
exit /b !ERR!
