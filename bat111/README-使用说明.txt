cd /d D:\QS\DL\bat

Get-ChildItem 'D:\QS\DL\bat' -Recurse -File | Unblock-File


# 量化数据每日增量下载（14个计划任务批处理）

- 批处理默认：
  - Conda 路径：D:\anaconda3
  - 环境名：quant
  - 工作目录：D:\2whm\src
  - 日志目录：D:\2whm\logs\daily  （自动创建）
  - 日志编码：UTF-8，文件名包含时间戳

- 使用方式：直接双击任何 .bat 会执行并在日志目录生成对应日志；任务计划程序会在运行完毕后自动关闭窗口。

## 建议的计划任务注册（命令行）
以 **管理员 PowerShell** 运行，按需修改运行时间（/ST）与路径（/TR）：

schtasks /Create /TN "QuantDaily-01-full_stock_basic" /TR "D:\QS\DL\bat\01_full_stock_basic.bat" /SC DAILY /ST 06:00 /RL HIGHEST /F
schtasks /Create /TN "QuantDaily-02-full_finance"     /TR "D:\QS\DL\bat\02_full_finance.bat"     /SC DAILY /ST 06:03 /RL HIGHEST /F
schtasks /Create /TN "QuantDaily-03-full_ths_index"   /TR "D:\QS\DL\bat\03_full_ths_index.bat"   /SC DAILY /ST 06:06 /RL HIGHEST /F
schtasks /Create /TN "QuantDaily-04-inc_dragon"       /TR "D:\QS\DL\bat\04_inc_dragon.bat"       /SC DAILY /ST 06:09 /RL HIGHEST /F
schtasks /Create /TN "QuantDaily-05-inc_stk_limit"    /TR "D:\QS\DL\bat\05_inc_stk_limit.bat"    /SC DAILY /ST 06:12 /RL HIGHEST /F
schtasks /Create /TN "QuantDaily-06-inc_stk_nineturn" /TR "D:\QS\DL\bat\06_inc_stk_nineturn.bat" /SC DAILY /ST 06:15 /RL HIGHEST /F
schtasks /Create /TN "QuantDaily-07-inc_ths_daily"    /TR "D:\QS\DL\bat\07_inc_ths_daily.bat"    /SC DAILY /ST 06:18 /RL HIGHEST /F
schtasks /Create /TN "QuantDaily-08-inc_index_weight" /TR "D:\QS\DL\bat\08_inc_index_weight.bat" /SC DAILY /ST 06:21 /RL HIGHEST /F
schtasks /Create /TN "QuantDaily-09-inc_adj_factor"   /TR "D:\QS\DL\bat\09_inc_adj_factor.bat"   /SC DAILY /ST 06:24 /RL HIGHEST /F
schtasks /Create /TN "QuantDaily-10-inc_daily_basic"  /TR "D:\QS\DL\bat\10_inc_daily_basic.bat"  /SC DAILY /ST 06:27 /RL HIGHEST /F
schtasks /Create /TN "QuantDaily-11-inc_index_daily"  /TR "D:\QS\DL\bat\11_inc_index_daily.bat"  /SC DAILY /ST 06:30 /RL HIGHEST /F
schtasks /Create /TN "QuantDaily-12-inc_moneyflow"    /TR "D:\QS\DL\bat\12_inc_moneyflow.bat"    /SC DAILY /ST 06:33 /RL HIGHEST /F
schtasks /Create /TN "QuantDaily-13-inc_stock_daily"  /TR "D:\QS\DL\bat\13_inc_stock_daily.bat"  /SC DAILY /ST 06:36 /RL HIGHEST /F
schtasks /Create /TN "QuantDaily-14-inc_suspend"      /TR "D:\QS\DL\bat\14_inc_suspend.bat"      /SC DAILY /ST 06:39 /RL HIGHEST /F

> 注意：
> 1) 把 /TR 后面的路径替换为你保存批处理的实际路径；建议集中放到 D:\QS\DL\bat\。
> 2) 如果需要使用指定账户运行：加上 `/RU "Administrator" /RP "你的密码"`；或在“任务计划程序”图形界面中设置“使用最高权限运行”。
> 3) 若需要每小时/每15分钟运行，改用 /SC HOURLY /MO 1 或 /SC MINUTE /MO 15。
> 4) 若 Python 脚本自身支持日期参数，可在对应 .bat 中增加 `--start 20250905` 等。

## 日志查看
- 日志文件位于 `D:\2whm\logs\daily`，命名如 `01-full_stock_basic_YYYYMMDD_HHMMSS.log`。
- 返回码 `code=0` 为成功，非 0 为失败。

## 故障排查
- 日志乱码：确认日志查看器使用 UTF-8；或将 `chcp 65001` 替换成 `chcp 936`。
- 找不到 conda：修改 `CONDA_ROOT`；或把 `call "%CONDA_ROOT%\Scripts\activate.bat"` 换成绝对路径。
- 权限不足：计划任务勾选“使用最高权限运行”，或在 schtasks 命令加 `/RL HIGHEST`。
