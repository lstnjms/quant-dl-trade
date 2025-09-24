# -*- coding: utf-8 -*-
"""
增量下载：龙虎榜每日明细（席位买卖 + 上榜原因）
- 从库内 MAX(trade_date_t) 的下一交易日起补齐到今天
- 新增：--qpm 限速；--upsert-every 批量入库
"""
import argparse, time
from typing import List, Optional
import numpy as np
import pandas as pd
from sqlalchemy import text
from utils import get_engine, get_pro, log, get_max_date, today_str

TABLE    = "dragon_t"
DATE_COL = "trade_date_t"

FIELDS_INST = "trade_date,ts_code,exalter,buy,sell,net_buy"
FIELDS_LIST = "trade_date,ts_code,reason"

class RateLimiter:
    def __init__(self, qpm: float = 480):
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
    if cal is None or cal.empty:
        return []
    return cal.sort_values("cal_date")["cal_date"].astype(str).tolist()

def _fetch_one_day(pro, limiter: RateLimiter, ymd: str) -> pd.DataFrame:
    limiter.wait()
    inst = pro.top_inst(trade_date=ymd, fields=FIELDS_INST)
    if inst is None or inst.empty:
        inst = pd.DataFrame(columns=["trade_date","ts_code","exalter","buy","sell","net_buy"])
    else:
        inst = inst.copy()

    limiter.wait()
    tlst = pro.top_list(trade_date=ymd, fields=FIELDS_LIST)
    if tlst is None or tlst.empty:
        tlst = pd.DataFrame(columns=["trade_date","ts_code","reason"])
    else:
        tlst = tlst.copy().sort_values("ts_code").drop_duplicates(["trade_date","ts_code"], keep="first")

    if inst.empty and tlst.empty:
        return pd.DataFrame(columns=[
            "ts_code_t","trade_date_t","exalter_t","buy_amount_t","sell_amount_t","net_amount_t","reason_t"
        ])

    m = pd.merge(inst, tlst[["trade_date","ts_code","reason"]], on=["trade_date","ts_code"], how="left")

    m["trade_date"] = m["trade_date"].astype(str).str.slice(0,8).apply(_to_date)
    for col in ["buy","sell","net_buy"]:
        m[col] = pd.to_numeric(m[col], errors="coerce")
    m["buy"]     = m["buy"].fillna(0.0)
    m["sell"]    = m["sell"].fillna(0.0)
    m["net_buy"] = m["net_buy"].fillna(0.0)
    if "exalter" in m.columns:
        m = m[m["exalter"].notna() & (m["exalter"].astype(str).str.strip()!="")]

    m.rename(columns={
        "ts_code":"ts_code_t",
        "trade_date":"trade_date_t",
        "exalter":"exalter_t",
        "buy":"buy_amount_t",
        "sell":"sell_amount_t",
        "net_buy":"net_amount_t",
        "reason":"reason_t",
    }, inplace=True)

    cols = ["ts_code_t","trade_date_t","exalter_t","buy_amount_t","sell_amount_t","net_amount_t","reason_t"]
    for c in cols:
        if c not in m.columns: m[c] = None
    return m[cols].copy()

def _upsert(engine, df: pd.DataFrame) -> int:
    if df is None or df.empty:
        return 0
    cols = ["ts_code_t","trade_date_t","exalter_t","buy_amount_t","sell_amount_t","net_amount_t","reason_t"]
    df = df.copy()
    for c in cols:
        if c not in df.columns: df[c] = None
    for c in ["buy_amount_t","sell_amount_t","net_amount_t"]:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)

    df = df.astype(object)
    df.replace({pd.NA: None, np.nan: None}, inplace=True)
    df.drop_duplicates(subset=["ts_code_t","trade_date_t","exalter_t"], keep="last", inplace=True)

    colq = ",".join(f"`{c}`" for c in cols)
    ph   = ",".join(f":{c}" for c in cols)
    upd_cols = [c for c in cols if c not in ("ts_code_t","trade_date_t","exalter_t")]
    upd  = ",".join(f"`{c}`=VALUES(`{c}`)" for c in upd_cols)
    sql  = f"INSERT INTO `{TABLE}` ({colq}) VALUES ({ph}) ON DUPLICATE KEY UPDATE {upd}"

    total = 0
    with engine.begin() as conn:
        for i in range(0, len(df), 10000):
            chunk = df.iloc[i:i+10000].copy().astype(object)
            chunk.replace({pd.NA: None, np.nan: None}, inplace=True)
            conn.execute(text(sql), chunk.to_dict(orient="records"))
            total += chunk.shape[0]
    return total

def main(qpm: float, upsert_every: int, init_start: str, start: Optional[str]=None, end: Optional[str]=None):
    pro = get_pro()
    eng = get_engine()
    limiter = RateLimiter(qpm=qpm)

    if end is None:
        end = today_str()
    if start is None:
        last = get_max_date(eng, TABLE, DATE_COL)     # YYYYMMDD or None
        start = init_start if last is None else last  # trade_cal 会给出下一交易日

    days = _trade_days(pro, start, end)
    if not days:
        log.info(f"[increment] table={TABLE} no trade days {start}~{end}")
        return
    # 若 start 恰好是库内最后交易日，取它在列表的下一个
    if last := get_max_date(eng, TABLE, DATE_COL):
        if last in days:
            days = days[days.index(last)+1:]

    log.info(f"[increment] qpm={qpm} upsert_every={upsert_every} trade_days={len(days)} range={start}~{end}")

    total = 0
    buf = []
    for i, ymd in enumerate(days, 1):
        df = _fetch_one_day(pro, limiter, ymd)
        if not df.empty:
            buf.append(df)
        if (i % upsert_every == 0) and buf:
            n = _upsert(eng, pd.concat(buf, ignore_index=True)); total += n; buf.clear()
            log.info(f"[increment] {i}/{len(days)} upsert rows={n} total={total}")

    if buf:
        n = _upsert(eng, pd.concat(buf, ignore_index=True)); total += n
        log.info(f"[increment] final upsert rows={n} total={total}")

    log.info(f"[increment] done table={TABLE} rows={total} range={start}~{end}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--qpm", type=float, default=480, help="每分钟最大请求数（建议≤500）")
    ap.add_argument("--upsert-every", type=int, default=10, help="每N个交易日合并一次入库")
    ap.add_argument("--init-start", default="20180101", help="库空默认起点(YYYYMMDD)")
    ap.add_argument("--start", default=None, help="覆盖增量起点(YYYYMMDD)")
    ap.add_argument("--end",   default=None, help="覆盖增量终点(YYYYMMDD)")
    args = ap.parse_args()
    main(args.qpm, args.upsert_every, args.init_start, args.start, args.end)
