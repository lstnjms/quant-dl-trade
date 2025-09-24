# -*- coding: utf-8 -*-
"""
全量下载：每日涨跌停价格（TuShare stk_limit）
- 粒度：逐交易日；先删后写；字段 *_t 映射；UPSERT 幂等；NaN/NA→None
- 新增：--index 指数代码（如 000905.SH），仅保留该指数历史成分股
- 新增：--qpm 每分钟请求上限（默认 480，适配 5000 积分档留余量）
"""
import argparse, time
from typing import List, Optional, Set
import numpy as np
import pandas as pd
from sqlalchemy import text
from utils import get_engine, get_pro, log, delete_range, today_str

TABLE    = "stk_limit"
DATE_COL = "trade_date_t"
FIELDS   = "ts_code,trade_date,pre_close,up_limit,down_limit"
DEFAULT_INIT_START = "20180101"

class RateLimiter:
    def __init__(self, qpm: float = 480.0):
        self.min_interval = 60.0 / max(1.0, qpm)
        self._t = 0.0
    def wait(self):
        now = time.perf_counter()
        dt  = now - self._t
        if dt < self.min_interval:
            time.sleep(self.min_interval - dt)
        self._t = time.perf_counter()

def _to_date(ymd: str) -> str:
    s = str(ymd)[:8]
    return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"

def _trade_days(pro, start: str, end: str) -> List[str]:
    cal = pro.trade_cal(start_date=start, end_date=end, is_open='1', fields="cal_date,is_open")
    if cal is None or cal.empty: return []
    return cal.sort_values("cal_date")["cal_date"].astype(str).tolist()

def _get_member_set(pro, index_code: Optional[str], start: str, end: str) -> Optional[Set[str]]:
    if not index_code:
        return None
    try:
        dfm = pro.index_weight(index_code=index_code, start_date=start, end_date=end, fields="con_code")
        if dfm is None or dfm.empty:
            log.warning(f"[members] index {index_code} got 0 members in {start}~{end}")
            return set()
        codes = set(dfm["con_code"].dropna().astype(str).unique().tolist())
        log.info(f"[members] index {index_code} unique codes={len(codes)} (historical union)")
        return codes
    except Exception as e:
        log.warning(f"[members] fetch index_weight failed: {e}")
        return set()

def _fetch_one_day(pro, ymd: str, member_set: Optional[Set[str]] = None) -> pd.DataFrame:
    df = pro.stk_limit(trade_date=ymd, fields=FIELDS)
    if df is None or df.empty:
        return pd.DataFrame(columns=FIELDS.split(","))
    df = df.copy()
    if member_set is not None and len(member_set) > 0:
        df = df[df["ts_code"].astype(str).isin(member_set)]
        if df.empty:
            return pd.DataFrame(columns=FIELDS.split(","))

    df["trade_date"] = df["trade_date"].astype(str).str.slice(0,8).apply(_to_date)
    for c in ["pre_close","up_limit","down_limit"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df.rename(columns={
        "ts_code":"ts_code_t",
        "trade_date":"trade_date_t",
        "pre_close":"pre_close_t",
        "up_limit":"up_limit_t",
        "down_limit":"down_limit_t",
    }, inplace=True)
    cols = ["ts_code_t","trade_date_t","pre_close_t","up_limit_t","down_limit_t"]
    for c in cols:
        if c not in df.columns: df[c] = None
    return df[cols].copy()

def _upsert(engine, df: pd.DataFrame) -> int:
    if df is None or df.empty: return 0
    cols = ["ts_code_t","trade_date_t","pre_close_t","up_limit_t","down_limit_t"]
    df = df.copy()
    for c in cols:
        if c not in df.columns: df[c] = None
    df = df[cols]

    df = df.astype(object)
    df.replace({pd.NA: None, np.nan: None}, inplace=True)
    df.drop_duplicates(subset=["ts_code_t","trade_date_t"], keep="last", inplace=True)

    colq = ",".join(f"`{c}`" for c in cols)
    ph   = ",".join(f":{c}" for c in cols)
    upd  = ",".join(f"`{c}`=VALUES(`{c}`)" for c in cols if c not in ("ts_code_t","trade_date_t"))
    sql  = f"INSERT INTO `{TABLE}` ({colq}) VALUES ({ph}) ON DUPLICATE KEY UPDATE {upd}"

    total = 0
    with engine.begin() as conn:
        for i in range(0, len(df), 5000):
            chunk = df.iloc[i:i+5000].copy().astype(object)
            chunk.replace({pd.NA: None, np.nan: None}, inplace=True)
            conn.execute(text(sql), chunk.to_dict(orient="records"))
            total += chunk.shape[0]
    return total

def main(start: str, end: str, index_code: str, qpm: float):
    pro = get_pro()
    eng = get_engine()
    limiter = RateLimiter(qpm=qpm)

    days = _trade_days(pro, start, end)
    if not days:
        log.info(f"[full] table={TABLE} no trade days {start}~{end}")
        return

    member_set = _get_member_set(pro, index_code.strip() or None, start, end)

    # 先按日期清区间（幂等）
    delete_range(eng, TABLE, DATE_COL, _to_date(days[0]), _to_date(days[-1]))
    log.info(f"[full] table={TABLE} index={index_code or 'ALL'} qpm={qpm} days={len(days)} range={start}~{end}")

    total = 0
    for i, ymd in enumerate(days, 1):
        try:
            limiter.wait()
            df = _fetch_one_day(pro, ymd, member_set)
            if df.empty:
                if i % 20 == 0: log.info(f"[full] {i}/{len(days)} {ymd} empty")
                continue
            n = _upsert(eng, df)
            total += n
            if n or (i % 20 == 0):
                log.info(f"[full] {i}/{len(days)} {ymd} rows={n} total={total}")
        except Exception as e:
            log.warning(f"[full] {i}/{len(days)} {ymd} error: {e}")
            time.sleep(0.2)

    log.info(f"[full] done table={TABLE} rows={total} range={start}~{end}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=DEFAULT_INIT_START, help="YYYYMMDD; default=20180101")
    ap.add_argument("--end",   default=today_str(),       help="YYYYMMDD; default=today")
    ap.add_argument("--index", default="", help="指数代码（如 000905.SH 中证500）；留空=全市场")
    ap.add_argument("--qpm", type=float, default=480, help="每分钟最大请求数，默认480")
    args = ap.parse_args()
    main(args.start.strip(), args.end.strip(), args.index.strip(), args.qpm)
