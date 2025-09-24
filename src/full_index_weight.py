# -*- coding: utf-8 -*-
"""全量：指数成分及权重（index_weight）
- 支持：--index_codes 逗号分隔；否则用 index_basic 按 --markets 拉取指数列表
- 区间：--start / --end（YYYYMMDD），逐指数拉取后入库
- 规范：区间先删；NaN->None；UPSERT 幂等
"""
import argparse, time, calendar
from typing import List, Tuple
import numpy as np
import pandas as pd
from sqlalchemy import text
from utils import get_engine, get_pro, log, delete_range, today_str

TABLE = "index_weight"
PKS   = ("index_code_t", "trade_date_t", "con_code_t")

# 仅保留文档给出的四个字段（不向接口要 con_name）
# https://tushare.pro/document/2?doc_id=96
FIELDS = "index_code,trade_date,con_code,weight"

def _to_date8(s: str) -> str:
    s = str(s)[:8]; return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"

def _parse_codes(s: str) -> List[str]:
    s = (s or "").strip()
    return [x.strip() for x in s.split(",") if x.strip()]

def _get_index_codes_by_markets(pro, markets: List[str]) -> List[str]:
    codes = []
    for m in markets:
        df = pro.index_basic(market=m, fields="ts_code")
        if df is not None and not df.empty:
            codes.extend(df["ts_code"].dropna().astype(str).tolist())
        time.sleep(0.05)
    return sorted(set(codes))

def _month_edges(day8: str) -> Tuple[str, str]:
    """给定 YYYYMMDD，返回该月 [YYYYMM01, YYYYMMDD_last]"""
    day8 = str(day8)[:8]
    y, m = int(day8[:4]), int(day8[4:6])
    first = f"{y:04d}{m:02d}01"
    last_day = calendar.monthrange(y, m)[1]
    last  = f"{y:04d}{m:02d}{last_day:02d}"
    return first, last

def _align_month_range(start8: str, end8: str) -> Tuple[str, str]:
    s1, _ = _month_edges(start8)
    _, e2 = _month_edges(end8)
    if int(s1) > int(e2):
        s1, e2 = e2, s1
    return s1, e2

def _fetch_one(pro, code: str, start: str, end: str) -> pd.DataFrame:
    # 对齐查询到月首/月末，避免“月度接口查不到数据”
    start_aligned, end_aligned = _align_month_range(start, end)

    df = pro.index_weight(
        index_code=code,
        start_date=start_aligned,
        end_date=end_aligned,
        fields=FIELDS
    )
    if df is None or df.empty:
        return pd.DataFrame(columns=FIELDS.split(","))

    df = df.copy()
    df["trade_date"] = df["trade_date"].astype(str).str.slice(0, 8).apply(_to_date8)
    if "weight" in df.columns:
        df["weight"] = pd.to_numeric(df["weight"], errors="coerce")

    df.rename(columns={
        "index_code":"index_code_t",
        "trade_date":"trade_date_t",
        "con_code":"con_code_t",
        # con_name 不由接口返回，这里不再重命名；后面统一补列为 None
        "weight":"weight_t",
    }, inplace=True)

    cols = ["index_code_t","trade_date_t","con_code_t","con_name_t","weight_t"]
    for c in cols:
        if c not in df.columns:
            df[c] = None  # con_name_t 在此补 None
    return df[cols].copy()

def _upsert(engine, df: pd.DataFrame) -> int:
    if df is None or df.empty: return 0
    cols = ["index_code_t","trade_date_t","con_code_t","con_name_t","weight_t"]
    for c in cols:
        if c not in df.columns: df[c] = None
    df = df[cols].astype(object).replace({pd.NA: None, np.nan: None})
    df.drop_duplicates(subset=list(PKS), keep="last", inplace=True)

    colq = ",".join(f"`{c}`" for c in cols)
    ph   = ",".join(f":{c}" for c in cols)
    upd  = ",".join(f"`{c}`=VALUES(`{c}`)" for c in cols if c not in PKS)
    sql  = f"INSERT INTO `{TABLE}` ({colq}) VALUES ({ph}) ON DUPLICATE KEY UPDATE {upd}"

    total = 0
    with engine.begin() as conn:
        for i in range(0, len(df), 3000):
            chunk = df.iloc[i:i+3000].astype(object).replace({pd.NA: None, np.nan: None})
            conn.execute(text(sql), chunk.to_dict(orient="records"))
            total += chunk.shape[0]
    return total

def main(start: str, end: str, index_codes: str, markets: str):
    pro = get_pro()
    eng = get_engine()

    codes = _parse_codes(index_codes)
    if not codes:
        mkts = [m.strip().upper() for m in (markets or "").split(",") if m.strip()] or ["SSE","SZSE"]
        codes = _get_index_codes_by_markets(pro, mkts)

    log.info(f"[full] table={TABLE} index_count={len(codes)} range={start}~{end}")

    # 先按日期范围清空（全指数）
    delete_range(eng, TABLE, "trade_date_t", _to_date8(start), _to_date8(end))

    total = 0
    for i, code in enumerate(codes, 1):
        try:
            df = _fetch_one(pro, code, start, end)
            n  = _upsert(eng, df)
            total += n
            if n or (i % 50 == 0):
                log.info(f"[full] {i}/{len(codes)} {code} rows={n} total={total}")
            time.sleep(0.08)
        except Exception as e:
            log.exception(f"[full] {i}/{len(codes)} {code} error: {e}")
            time.sleep(0.15)
    log.info(f"[full] done table={TABLE} rows={total} range={start}~{end}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--start",       default="19900101", help="YYYYMMDD")
    ap.add_argument("--end",         default=today_str(), help="YYYYMMDD")
    ap.add_argument("--index_codes", default="", help="逗号分隔的指数代码列表（留空则按 --markets 获取）")
    ap.add_argument("--markets",     default="SSE,SZSE", help="用 index_basic 拉取指数代码的市场列表，默认 SSE,SZSE")
    args = ap.parse_args()
    main(args.start.strip(), args.end.strip(), args.index_codes.strip(), args.markets.strip())
