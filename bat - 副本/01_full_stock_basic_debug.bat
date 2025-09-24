@echo off
setlocal EnableExtensions EnableDelayedExpansion

rem ===== 修改成你机器的实际路径（来自 `where conda.bat` 的结果）=====
set "CONDA_BAT=C:\conda.bat"
set "CONDA_ENV=quant"

rem ===== 你的项目路径 =====
set "SRC_DIR=D:\QS\DL\src"
set "PY_SCRIPT=full_stock_basic.py"

echo [1] 檢查 conda.bat: %CONDA_BAT%
if not exist "%CONDA_BAT%" (
  echo ERROR: 未找到 conda.bat，請先在 CMD 執行 ^> where conda.bat 取實際路徑
  set EXITSTEP=1 & goto :END
)

echo [2] 激活環境: %CONDA_ENV%
call "%CONDA_BAT%" activate "%CONDA_ENV%"
if errorlevel 1 (
  echo ERROR: conda activate 失敗（環境名可能不存在：%CONDA_ENV%）
  set EXITSTEP=2 & goto :END
)

echo [3] 驗證 python：
where python || (echo ERROR: python 不在 PATH（環境未激活成功） & set EXITSTEP=3 & goto :END)
python --version

echo [4] 切換到腳本目錄：%SRC_DIR%
if not exist "%SRC_DIR%" (
  echo ERROR: 目錄不存在：%SRC_DIR%
  set EXITSTEP=4 & goto :END
)
pushd "%SRC_DIR%"

echo [5] 檢查腳本：%PY_SCRIPT%
if not exist "%PY_SCRIPT%" (
  echo ERROR: 未找到腳本：%SRC_DIR%\%PY_SCRIPT%
  set EXITSTEP=5 & goto :END
)

echo [6] 執行 python %PY_SCRIPT% …
python "%PY_SCRIPT%"
set EXITCODE=%errorlevel%
echo 程序返回碼：%EXITCODE%

:END
echo 調試步驟標記 EXITSTEP=%EXITSTEP%
popd >nul 2>nul
pause
exit /b %EXITCODE%
