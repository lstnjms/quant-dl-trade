# -*- coding: utf-8 -*-
"""
全量下载：龙虎榜每日明细（席位买卖 + 上榜原因）
- 接口：pro.top_inst（席位买卖）、pro.top_list（上榜原因）
- 粒度：逐交易日；主键唯一：ts_code_t + trade_date_t + exalter_t
- 新增：
  * --qpm 限速（requests per minute，默认480，适配5000积分档）
  * --upsert-every 每N个交易日合并一次入库（默认10）
"""
import argparse, time
from typing import List
import numpy as np
import pandas as pd
from sqlalchemy import text
from utils import get_engine, get_pro, log, delete_range, today_str

TABLE    = "dragon_t"
DATE_COL = "trade_date_t"

FIELDS_INST = "trade_date,ts_code,exalter,buy,sell,net_buy"
FIELDS_LIST = "trade_date,ts_code,reason"

class RateLimiter:
    """简单限速：按QPM均匀节流"""
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
    # 机构/营业部成交金额（席位）
    limiter.wait()
    inst = pro.top_inst(trade_date=ymd, fields=FIELDS_INST)
    if inst is None or inst.empty:
        inst = pd.DataFrame(columns=["trade_date","ts_code","exalter","buy","sell","net_buy"])
    else:
        inst = inst.copy()

    # 上榜原因（按股票日，去重取首条）
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

    m = pd.merge(inst, tlst[["trade_date","ts_code","reason"]],
                 on=["trade_date","ts_code"], how="left")

    m["trade_date"] = m["trade_date"].astype(str).str.slice(0,8).apply(_to_date)
    for col in ["buy","sell","net_buy"]:
        m[col] = pd.to_numeric(m[col], errors="coerce")
    m["buy"]     = m["buy"].fillna(0.0)
    m["sell"]    = m["sell"].fillna(0.0)
    m["net_buy"] = m["net_buy"].fillna(0.0)
    # 去掉没有席位名的行（无法构成唯一键）
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
    # 金额列保证非空（NOT NULL）
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
        for i in range(0, len(df), 10000):  # 提大批次，减少DB压力
            chunk = df.iloc[i:i+10000].copy().astype(object)
            chunk.replace({pd.NA: None, np.nan: None}, inplace=True)
            conn.execute(text(sql), chunk.to_dict(orient="records"))
            total += chunk.shape[0]
    return total

def main(start: str, end: str, qpm: float, upsert_every: int):
    pro = get_pro()
    eng = get_engine()
    limiter = RateLimiter(qpm=qpm)

    days = _trade_days(pro, start, end)
    if not days:
        log.info(f"[full] table={TABLE} no trade days {start}~{end}")
        return

    # 按交易日清区间（幂等）
    delete_range(eng, TABLE, DATE_COL, _to_date(days[0]), _to_date(days[-1]))
    log.info(f"[full] qpm={qpm} upsert_every={upsert_every} days={len(days)} range={start}~{end}")

    total = 0
    buf = []
    for i, ymd in enumerate(days, 1):
        df = _fetch_one_day(pro, limiter, ymd)
        if not df.empty:
            buf.append(df)
        # 每N天合并一次入库
        if (i % upsert_every == 0) and buf:
            n = _upsert(eng, pd.concat(buf, ignore_index=True)); total += n; buf.clear()
            log.info(f"[full] {i}/{len(days)} upsert rows={n} total={total}")

    if buf:
        n = _upsert(eng, pd.concat(buf, ignore_index=True)); total += n
        log.info(f"[full] final upsert rows={n} total={total}")

    log.info(f"[full] done table={TABLE} rows={total} range={start}~{end}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="20180101", help="YYYYMMDD; default=20180101")
    ap.add_argument("--end",   default=today_str(), help="YYYYMMDD; default=today")
    ap.add_argument("--qpm", type=float, default=480, help="每分钟最大请求数（建议≤500）")
    ap.add_argument("--upsert-every", type=int, default=10, help="每N个交易日合并一次入库")
    args = ap.parse_args()
    main(args.start.strip(), args.end.strip(), args.qpm, args.upsert_every)
