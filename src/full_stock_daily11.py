# -*- coding: utf-8 -*-
"""全量下载：stock_daily（逐交易日，含复权因子，先删区间数据再重下）
- 数据源：TuShare daily + adj_factor（按同日合并）
- 表：stock_daily（复合主键 ts_code_t + trade_date_t）
- 策略：按交易日逐日抓取，UPSERT 写入（复合主键安全幂等）
"""
import argparse, time
from datetime import date
from typing import List
import pandas as pd
from sqlalchemy import text
from utils import get_engine, get_pro, log, delete_range, today_str

TABLE    = "stock_daily"
DATE_COL = "trade_date_t"

FIELDS_DAILY = "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount"
FIELDS_ADJ   = "ts_code,trade_date,adj_factor"

def _to_date(d: str) -> str:
    """YYYYMMDD -> YYYY-MM-DD"""
    return f"{d[0:4]}-{d[4:6]}-{d[6:8]}"

def _trade_days(pro, start: str, end: str) -> List[str]:
    cal = pro.trade_cal(start_date=start, end_date=end, is_open='1')
    return cal.sort_values("cal_date")["cal_date"].tolist()

def _fetch_one_day(pro, ymd: str) -> pd.DataFrame:
    d = pro.daily(trade_date=ymd, fields=FIELDS_DAILY)
    if d is None or d.empty:
        return pd.DataFrame()
    af = pro.adj_factor(trade_date=ymd, fields=FIELDS_ADJ)
    if af is None or af.empty:
        af = pd.DataFrame(columns=["ts_code","trade_date","adj_factor"])
    m = pd.merge(d, af, on=["ts_code","trade_date"], how="left")
    # 映射到库表字段
    need = ["ts_code","trade_date","open","high","low","close","pre_close","change","pct_chg","vol","amount","adj_factor"]
    for c in need:
        if c not in m.columns:
            m[c] = None
    m = m[need].copy()
    m["trade_date"] = m["trade_date"].astype(str).str.slice(0,8).apply(_to_date)
    m.rename(columns={
        "ts_code":"ts_code_t","trade_date":"trade_date_t","open":"open_t","high":"high_t",
        "low":"low_t","close":"close_t","pre_close":"pre_close_t","change":"change_t",
        "pct_chg":"pct_chg_t","vol":"vol_t","amount":"amount_t","adj_factor":"adj_factor_t"
    }, inplace=True)
    return m

def _upsert_daily(engine, df: pd.DataFrame) -> int:
    """复合主键 UPSERT（不更新主键列）"""
    if df is None or df.empty:
        return 0
    cols = list(df.columns)
    # 排除两个主键列
    update_cols = [c for c in cols if c not in ("ts_code_t","trade_date_t")]
    col_quoted = ",".join(f"`{c}`" for c in cols)
    placeholders = ",".join(f":{c}" for c in cols)
    update_clause = ",".join(f"`{c}`=VALUES(`{c}`)" for c in update_cols)
    sql = (
        f"INSERT INTO `{TABLE}` ({col_quoted}) VALUES ({placeholders}) "
        f"ON DUPLICATE KEY UPDATE {update_clause}"
    )
    total = 0
    with engine.begin() as conn:
        for i in range(0, len(df), 1000):
            chunk = df.iloc[i:i+1000].copy()
            vals = chunk.to_dict(orient="records")
            n = conn.execute(text(sql), vals).rowcount or 0
            total += (chunk.shape[0])
    return total

def main(start: str, end: str):
    pro = get_pro()
    eng = get_engine()

    log.info(f"[full] table={TABLE} range={start}~{end} deleting...")
    delete_range(eng, TABLE, DATE_COL, f"{start[:4]}-{start[4:6]}-{start[6:8]}", f"{end[:4]}-{end[4:6]}-{end[6:8]}")

    days = _trade_days(pro, start, end)
    log.info(f"[full] table={TABLE} trade_days={len(days)}")
    total = 0
    for i, d in enumerate(days, 1):
        df = _fetch_one_day(pro, d)
        if df is None or df.empty:
            log.info(f"[full] {i}/{len(days)} {d} empty")
            time.sleep(0.4)
            continue
        rows = _upsert_daily(eng, df)
        total += rows
        log.info(f"[full] {i}/{len(days)} {d} upsert rows={rows} total={total}")
        time.sleep(0.4)
    log.info(f"[full] done table={TABLE} rows={total} range={start}~{end}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="19700101", help="YYYYMMDD; default=19700101")
    ap.add_argument("--end",   default=today_str(), help="YYYYMMDD; default=today")
    args = ap.parse_args()
    main(args.start.strip(), args.end.strip())
