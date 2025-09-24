@echo off
setlocal
call ".\01_full_stock_basic.bat"
call ".\02_full_finance.bat"
call ".\03_full_ths_index.bat"
call ".\04_inc_dragon.bat"
call ".\05_inc_stk_limit.bat"
call ".\06_inc_stk_nineturn.bat"
call ".\07_inc_ths_daily.bat"
call ".\08_inc_index_weight.bat"
call ".\09_inc_adj_factor.bat"
call ".\10_inc_daily_basic.bat"
call ".\11_inc_index_daily.bat"
call ".\12_inc_moneyflow.bat"
call ".\13_inc_stock_daily.bat"
call ".\14_inc_suspend.bat"
echo All done.
exit /b 0
