# -*- coding: utf-8 -*-
"""
全量回刷模式：A股财务指标（TuShare VIP 批量）
- 自动读取库内最大 end_date_t，向前回刷 N 个月到今天
- 使用 fina_indicator_vip 按日期分段批量抓全市场
- 安全日期转换（缺失返回 None），百分比异常值清洗
- 库空默认起始日：2018-01-01（可用 --init-start 覆盖）
"""
import argparse
import time
import datetime as dt
import pandas as pd
import numpy as np
from sqlalchemy import text
from dateutil.relativedelta import relativedelta

from utils import get_engine, get_pro, log, delete_range, today_str, get_max_date  # 项目公用

TABLE = "finance"
PKS = ("ts_code_t", "end_date_t")
DATE_COL = "end_date_t"
FIELDS_IND = (
    "ts_code,ann_date,f_ann_date,end_date,"
    "total_revenue,operate_profit,net_profit,total_assets,total_liab,roe,roa,gross_margin"
)

DEFAULT_INIT_START = "20180101"  # 库空默认起始日

def _to_date8(x):
    if x is None:
        return None
    try:
        if (isinstance(x, float) and np.isnan(x)) or pd.isna(x):
            return None
    except Exception:
        pass
    s = str(x).strip()
    if s == "" or s.lower() in ("none", "nat", "nan"):
        return None
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) < 8:
        return None
    y, m, d = digits[:4], digits[4:6], digits[6:8]
    if y == "0000" or m == "00" or d == "00":
        return None
    return f"{y}-{m}-{d}"

def _chunks_by_days(start: str, end: str, step_days: int):
    d0 = dt.datetime.strptime(start, "%Y%m%d").date()
    d1 = dt.datetime.strptime(end, "%Y%m%d").date()
    cur = d0
    while cur <= d1:
        nxt = min(cur + dt.timedelta(days=step_days - 1), d1)
        yield cur.strftime("%Y%m%d"), nxt.strftime("%Y%m%d")
        cur = nxt + dt.timedelta(days=1)

def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=[
            "ts_code_t","ann_date_t","f_ann_date_t","end_date_t",
            "total_revenue_t","operate_profit_t","net_profit_t",
            "total_assets_t","total_liab_t","roe_t","roa_t","gross_margin_t"
        ])

    df = df.copy()

    for c in ["ann_date", "f_ann_date", "end_date"]:
        if c in df.columns:
            df[c] = df[c].apply(_to_date8)

    df.rename(columns={
        "ts_code": "ts_code_t",
        "ann_date": "ann_date_t",
        "f_ann_date": "f_ann_date_t",
        "end_date": "end_date_t",
        "total_revenue": "total_revenue_t",
        "operate_profit": "operate_profit_t",
        "net_profit": "net_profit_t",
        "total_assets": "total_assets_t",
        "total_liab": "total_liab_t",
        "roe": "roe_t",
        "roa": "roa_t",
        "gross_margin": "gross_margin_t",
    }, inplace=True)

    num_cols = [
        "total_revenue_t","operate_profit_t","net_profit_t",
        "total_assets_t","total_liab_t","roe_t","roa_t","gross_margin_t"
    ]
    for c in num_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    for c in ["roe_t", "roa_t", "gross_margin_t"]:
        if c in df.columns:
            df.loc[df[c].abs() > 1000, c] = None

    cols = ["ts_code_t","ann_date_t","f_ann_date_t","end_date_t",
            "total_revenue_t","operate_profit_t","net_profit_t",
            "total_assets_t","total_liab_t","roe_t","roa_t","gross_margin_t"]
    for c in cols:
        if c not in df.columns:
            df[c] = None
    return df[cols]

def _upsert(engine, df: pd.DataFrame) -> int:
    if df is None or df.empty:
        return 0
    cols = ["ts_code_t","ann_date_t","f_ann_date_t","end_date_t",
            "total_revenue_t","operate_profit_t","net_profit_t",
            "total_assets_t","total_liab_t","roe_t","roa_t","gross_margin_t"]
    df = df[cols].astype(object).replace({pd.NA: None, np.nan: None})
    df.drop_duplicates(subset=list(PKS), keep="last", inplace=True)

    colq = ",".join(f"`{c}`" for c in cols)
    ph   = ",".join(f":{c}" for c in cols)
    upd  = ",".join(f"`{c}`=VALUES(`{c}`)" for c in cols if c not in PKS)
    sql  = f"INSERT INTO `{TABLE}` ({colq}) VALUES ({ph}) ON DUPLICATE KEY UPDATE {upd}"

    total = 0
    with engine.begin() as conn:
        for i in range(0, len(df), 5000):
            chunk = df.iloc[i:i+5000].astype(object).replace({pd.NA: None, np.nan: None})
            conn.execute(text(sql), chunk.to_dict(orient="records"))
            total += chunk.shape[0]
    return total

def _vip_fetch(pro, s: str, e: str) -> pd.DataFrame:
    return pro.fina_indicator_vip(start_date=s, end_date=e, fields=FIELDS_IND)

def main(refresh_months: int, chunk_days: int, init_start: str):
    pro = get_pro()
    eng = get_engine()

    end = today_str()
    max_db = get_max_date(eng, TABLE, DATE_COL)
    if max_db:
        d_start = (dt.datetime.strptime(max_db, "%Y%m%d").date()
                   - relativedelta(months=refresh_months))
        start = d_start.strftime("%Y%m%d")
    else:
        start = init_start or DEFAULT_INIT_START

    log.info(f"[full-auto] table={TABLE} refresh_months={refresh_months} range={start}~{end}")

    def _fmt(y4md8): 
        return f"{y4md8[:4]}-{y4md8[4:6]}-{y4md8[6:8]}"
    delete_range(eng, TABLE, DATE_COL, _fmt(start), _fmt(end))

    total = 0
    for s, e in _chunks_by_days(start, end, chunk_days):
        try:
            raw = _vip_fetch(pro, s, e)
            df  = _normalize(raw)
            n   = _upsert(eng, df)
            total += n
            log.info(f"[full-auto] {s}~{e} rows={n} total={total}")
            time.sleep(0.1)
        except Exception as ex:
            log.exception(f"[full-auto] {s}~{e} error: {ex}")
            time.sleep(0.2)

    log.info(f"[full-auto] done table={TABLE} rows={total} range={start}~{end}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh-months", type=int, default=12, help="回刷窗口（月），默认12")
    ap.add_argument("--chunk", type=int, default=31, help="按天分段抓取窗口，默认31天")
    ap.add_argument("--init-start", default=DEFAULT_INIT_START, help="库空时默认起始日(YYYYMMDD)")
    args = ap.parse_args()
    main(args.refresh_months, args.chunk, args.init_start)
