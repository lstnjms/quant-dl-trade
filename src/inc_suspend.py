# -*- coding: utf-8 -*-
"""增量下载：每日停复牌（逐交易日补齐）"""
import time
from typing import List, Optional
import pandas as pd
from sqlalchemy import text
from utils import get_engine, get_pro, log, get_max_date, today_str

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
            chunk = df.iloc[i:i+1000].copy()   # ✅ iloc 切片，避免语法错误
            conn.execute(text(sql), chunk.to_dict(orient="records"))
            total += chunk.shape[0]
    return total

def main(start: Optional[str]=None, end: Optional[str]=None):
    pro = get_pro()
    eng = get_engine()
    if end is None:
        end = today_str()
    if start is None:
        last = get_max_date(eng, TABLE, DATE_COL)  # 返回 YYYYMMDD 或 None
        if last is None:
            start = "19900101"
        else:
            cal = _trade_days(pro, last, end)
            if not cal:
                log.info(f"[increment] table={TABLE} already up-to-date last={last}")
                return
            if last in cal:
                idx = cal.index(last) + 1
                if idx >= len(cal):
                    log.info(f"[increment] table={TABLE} already up-to-date last={last}")
                    return
                start = cal[idx]
            else:
                start = cal[0]

    days = _trade_days(pro, start, end)
    log.info(f"[increment] table={TABLE} range={start}~{end} trade_days={len(days)}")
    total = 0
    for i, ymd in enumerate(days, 1):
        df = _fetch_one_day(pro, ymd)
        if df is None or df.empty:
            log.info(f"[increment] {i}/{len(days)} {ymd} empty")
            time.sleep(0.25); continue
        n  = _upsert(eng, df)
        total += n
        log.info(f"[increment] {i}/{len(days)} {ymd} upsert rows={n} total={total}")
        time.sleep(0.25)
    log.info(f"[increment] done table={TABLE} rows={total} range={start}~{end}")

if __name__ == "__main__":
    main()
