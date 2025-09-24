@echo off
setlocal EnableExtensions EnableDelayedExpansion
set "CONDA_BAT=D:\anaconda3\condabin\conda.bat"
set "CONDA_ENV=quant"
set "SRC=D:\QS\DL\src"
set "LOGDIR=D:\QS\DL\logs\daily"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
for /f "tokens=1-4 delims=/:. " %%a in ("%date% %time%") do set "TS=%%a%%b%%c_%%d"
set "LOG=%LOGDIR%\02-full-finance-%TS%.log"
call "%CONDA_BAT%" activate "%CONDA_ENV%" >>"%LOG%" 2>&1
if errorlevel 1 exit /b 9009
pushd "%SRC%"
python full_finance.py >>"%LOG%" 2>&1
set "ERR=%errorlevel%"
popd
echo ExitCode=%ERR%>>"%LOG%"
echo ExitCode=%ERR%
exit /b %ERR%
