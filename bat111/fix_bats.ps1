
param(
  [string]$Dir = "D:\QS\DL\bat",
  [string]$SrcDir = "D:\QS\DL\src",
  [string]$LogDir = "D:\QS\DL\logs\daily",
  [string]$CondaBat = "D:\anaconda3\condabin\conda.bat",
  [string]$Env = "quant",
  [string]$BackupSuffix = "_old",
  [switch]$DryRun
)

Write-Host "Target dir: $Dir"
if (!(Test-Path $Dir)) { throw "目录不存在：$Dir" }
if (!(Test-Path $LogDir)) { New-Item -ItemType Directory -Force -Path $LogDir | Out-Null }

# 模板（UTF-8 无 BOM 输出）
$tpl = @'
@echo off
setlocal EnableExtensions EnableDelayedExpansion
set "CONDA_BAT={CONDA_BAT}"
set "CONDA_ENV={ENV}"
set "SRC={SRC}"
set "LOGDIR={LOGDIR}"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
for /f "tokens=1-4 delims=/:. " %%a in ("%date% %time%") do set "TS=%%a%%b%%c_%%d"
set "LOG=%LOGDIR%\{LOGPREFIX}-%TS%.log"
call "%CONDA_BAT%" activate "%CONDA_ENV%" >>"%LOG%" 2>&1
if errorlevel 1 exit /b 9009
pushd "%SRC%"
python {PYSCRIPT} >>"%LOG%" 2>&1
set "ERR=%errorlevel%"
popd
echo ExitCode=%ERR%>>"%LOG%"
echo ExitCode=%ERR%
exit /b %ERR%
'@

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

$batFiles = Get-ChildItem $Dir -Filter *.bat | Sort-Object Name
if ($batFiles.Count -eq 0) { throw "该目录里没有 .bat 文件：$Dir" }

$callLines = @()
$report = @()

foreach ($f in $batFiles) {
  $name = $f.Name
  $base = $f.BaseName

  # 推断 Python 脚本名：去掉前缀编号与下划线（如：01_full_stock_basic → full_stock_basic.py）
  $parts = $base -split "_", 2
  $pyBase = if ($parts.Length -ge 2) { $parts[1] } else { $parts[0] }
  $py = "$pyBase.py"

  $logPrefix = ($base -replace "_","-")

  $backup = Join-Path $Dir ($base + $BackupSuffix + ".bat")
  $newbat = $f.FullName

  if ($DryRun) {
    $report += "DRYRUN: $name  →  backup=$([IO.Path]::GetFileName($backup))  | py=$py"
    continue
  }

  try {
    # 备份原文件（加后缀）
    Copy-Item $f.FullName $backup -Force

    # 生成新内容并覆盖原文件名（可直接用）
    $content = $tpl.Replace("{CONDA_BAT}", $CondaBat).
                    Replace("{ENV}", $Env).
                    Replace("{SRC}", $SrcDir).
                    Replace("{LOGDIR}", $LogDir).
                    Replace("{LOGPREFIX}", $logPrefix).
                    Replace("{PYSCRIPT}", $py)

    [System.IO.File]::WriteAllText($newbat, $content, $utf8NoBom)

    # 解除阻止
    Unblock-File -Path $newbat -ErrorAction SilentlyContinue

    $callLines += 'call ".\' + $name + '"'
    $report += "OK: $name  |  backup=$([IO.Path]::GetFileName($backup))  |  py=$py"
  }
  catch {
    $report += "FAIL: $name  |  $($_.Exception.Message)"
  }
}

# 生成 run_all.bat
if (-not $DryRun) {
  $runAll = "@echo off`r`nsetlocal`r`n" + ($callLines -join "`r`n") + "`r`necho All done.`r`nexit /b 0`r`n"
  [System.IO.File]::WriteAllText((Join-Path $Dir "run_all.bat"), $runAll, $utf8NoBom)
  Unblock-File -Path (Join-Path $Dir "run_all.bat") -ErrorAction SilentlyContinue
}

$report | ForEach-Object { Write-Host $_ }
