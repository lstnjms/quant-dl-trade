# -*- coding: utf-8 -*-
"""全量下载：每日停复牌（逐交易日，全市场）
- 接口：pro.suspend_d（https://tushare.pro/document/2?doc_id=214）
- 表：suspend（主键：ts_code_t + trade_date_t + suspend_type_t）
- 策略：按交易日逐日抓取；区间内先 DELETE 再重下；UPSERT 入库。
"""
import argparse, time
from typing import List
import pandas as pd
from sqlalchemy import text
from utils import get_engine, get_pro, log, delete_range, today_str

TABLE    = "suspend"
DATE_COL = "trade_date_t"

FIELDS = "ts_code,trade_date,suspend_timing,suspend_type"
NEED_SRC = ["ts_code","trade_date","suspend_timing","suspend_type"]
NEED_DST = ["ts_code_t","trade_date_t","suspend_timing_t","suspend_type_t"]

def _to_date(ymd: str) -> str:
    return f"{ymd[0:4]}-{ymd[4:6]}-{ymd[6:8]}"

def _trade_days(pro, start: str, end: str) -> List[str]:
    cal = pro.trade_cal(start_date=start, end_date=end, is_open='1')
    return cal.sort_values("cal_date")["cal_date"].tolist()

def _fetch_one_day(pro, ymd: str) -> pd.DataFrame:
    df = pro.suspend_d(trade_date=ymd, fields=FIELDS)
    if df is None or df.empty:
        return pd.DataFrame(columns=NEED_SRC)
    for c in NEED_SRC:
        if c not in df.columns: df[c] = None
    df = df[NEED_SRC].copy()
    df["trade_date"] = df["trade_date"].astype(str).str.slice(0,8).apply(_to_date)
    df.rename(columns={
        "ts_code":"ts_code_t",
        "trade_date":"trade_date_t",
        "suspend_timing":"suspend_timing_t",
        "suspend_type":"suspend_type_t"
    }, inplace=True)
    return df

def _upsert(engine, df: pd.DataFrame) -> int:
    if df is None or df.empty: return 0
    for c in NEED_DST:
        if c not in df.columns: df[c] = None
    df = df[NEED_DST].copy()
    cols = NEED_DST
    colq = ",".join(f"`{c}`" for c in cols)
    ph   = ",".join(f":{c}" for c in cols)
    upd_cols = [c for c in cols if c not in ("ts_code_t","trade_date_t","suspend_type_t")]
    upd  = ",".join(f"`{c}`=VALUES(`{c}`)" for c in upd_cols)
    sql  = f"INSERT INTO `{TABLE}` ({colq}) VALUES ({ph}) ON DUPLICATE KEY UPDATE {upd}"
    total = 0
    with engine.begin() as conn:
        for i in range(0, len(df), 1000):
            chunk = df.iloc[i:i+1000].copy()
            conn.execute(text(sql), chunk.to_dict(orient="records"))
            total += chunk.shape[0]
    return total

def main(start: str, end: str):
    pro = get_pro()
    eng = get_engine()

    log.info(f"[full] table={TABLE} delete_range {start}~{end}")
    delete_range(eng, TABLE, DATE_COL, _to_date(start), _to_date(end))

    days = _trade_days(pro, start, end)
    log.info(f"[full] table={TABLE} trade_days={len(days)}")
    total = 0
    for i, ymd in enumerate(days, 1):
        df = _fetch_one_day(pro, ymd)
        if df is None or df.empty:
            log.info(f"[full] {i}/{len(days)} {ymd} empty")
            time.sleep(0.25); continue
        n = _upsert(eng, df)
        total += n
        log.info(f"[full] {i}/{len(days)} {ymd} upsert rows={n} total={total}")
        time.sleep(0.25)
    log.info(f"[full] done table={TABLE} rows={total} range={start}~{end}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="19700101", help="YYYYMMDD; default=19700101")
    ap.add_argument("--end",   default=today_str(), help="YYYYMMDD; default=today")
    args = ap.parse_args()
    main(args.start.strip(), args.end.strip())
