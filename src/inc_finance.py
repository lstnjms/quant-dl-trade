# -*- coding: utf-8 -*-
"""增量下载：财务指标（按“报告期 end_date”推进）
- 起点：库内 MAX(end_date_t) 的下一报告期（若空，从 1990Q1 起）
- 终点：当前应披露的最近报告期（按自然日推算最近季末）
"""
import time, datetime as dt
from typing import List, Optional
import numpy as np
import pandas as pd
from sqlalchemy import text
from utils import get_engine, get_pro, log, get_max_date, today_str

TABLE = "finance"
PKS   = ("ts_code_t", "end_date_t")
SCALE_TO_WANYUAN = True

FIELDS_IND = "ts_code,ann_date,f_ann_date,end_date,roe,roa,grossprofit_margin"
FIELDS_INC = "ts_code,ann_date,f_ann_date,end_date,total_revenue,operate_profit,n_income"
FIELDS_BS  = "ts_code,ann_date,f_ann_date,end_date,total_assets,total_liab"

NEED_DST = [
    "ts_code_t","ann_date_t","f_ann_date_t","end_date_t",
    "total_revenue_t","operate_profit_t","net_profit_t",
    "total_assets_t","total_liab_t",
    "roe_t","roa_t","gross_margin_t"
]

def _ymd_to_date(s: str) -> str:
    s = str(s)[:8]
    return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"

def _month_end(y: int, m: int) -> dt.date:
    if m == 12:
        return dt.date(y, 12, 31)
    first_next = dt.date(y, m + 1, 1)
    return first_next - dt.timedelta(days=1)

def _next_period(period_ymd: Optional[str]) -> str:
    """给定 YYYYMMDD（季末），返回下一季的季末；None 则 19900331"""
    if not period_ymd:
        return "19900331"
    d = dt.datetime.strptime(period_ymd, "%Y%m%d").date()
    if d.month == 3:
        return _month_end(d.year, 6).strftime("%Y%m%d")
    if d.month == 6:
        return _month_end(d.year, 9).strftime("%Y%m%d")
    if d.month == 9:
        return _month_end(d.year, 12).strftime("%Y%m%d")
    return _month_end(d.year + 1, 3).strftime("%Y%m%d")

def _latest_period_until_today() -> str:
    """截至今天的最近季末"""
    today = dt.date.today()
    ends = [_month_end(today.year, 3), _month_end(today.year, 6),
            _month_end(today.year, 9), _month_end(today.year, 12)]
    ends = [e for e in ends if e <= today]
    if not ends:
        return dt.date(today.year - 1, 12, 31).strftime("%Y%m%d")
    return max(ends).strftime("%Y%m%d")

def _scale_to_wy(df: pd.DataFrame, cols: List[str]) -> pd.DataFrame:
    if not SCALE_TO_WANYUAN or df is None or df.empty:
        return df
    for c in cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce") / 10000.0
    return df

def _normalize_and_rename(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=NEED_DST)
    df = df.copy()
    for c in ["ann_date","f_ann_date","end_date"]:
        if c in df.columns:
            df[c] = df[c].apply(lambda x: _ymd_to_date(x) if pd.notnull(x) else None)
    df = _scale_to_wy(df, ["total_revenue","operate_profit","net_profit","total_assets","total_liab"])
    df.rename(columns={
        "ts_code":"ts_code_t",
        "ann_date":"ann_date_t",
        "f_ann_date":"f_ann_date_t",
        "end_date":"end_date_t",
        "total_revenue":"total_revenue_t",
        "operate_profit":"operate_profit_t",
        "net_profit":"net_profit_t",
        "total_assets":"total_assets_t",
        "total_liab":"total_liab_t",
        "roe":"roe_t",
        "roa":"roa_t",
        "gross_margin":"gross_margin_t",
    }, inplace=True)
    for c in NEED_DST:
        if c not in df.columns: df[c] = None
    return df[NEED_DST]

# ---------- 入库（方案A：astype(object)+NaN/NA→None） ----------
def _upsert(engine, df: pd.DataFrame) -> int:
    if df is None or df.empty:
        return 0
    cols = NEED_DST
    df = df.copy()
    for c in cols:
        if c not in df.columns:
            df[c] = None
    df = df[cols].copy()
    df = df.astype(object)
    df.replace({pd.NA: None, np.nan: None}, inplace=True)
    df.drop_duplicates(subset=list(PKS), keep='last', inplace=True)

    colq = ",".join(f"`{c}`" for c in cols)
    ph   = ",".join(f":{c}" for c in cols)
    upd_cols = [c for c in cols if c not in PKS]
    upd  = ",".join(f"`{c}`=VALUES(`{c}`)" for c in upd_cols)
    sql  = f"INSERT INTO `{TABLE}` ({colq}) VALUES ({ph}) ON DUPLICATE KEY UPDATE {upd}"

    total = 0
    with engine.begin() as conn:
        for i in range(0, len(df), 800):
            chunk = df.iloc[i:i+800].copy()
            chunk = chunk.astype(object)
            chunk.replace({pd.NA: None, np.nan: None}, inplace=True)
            conn.execute(text(sql), chunk.to_dict(orient="records"))
            total += chunk.shape[0]
    return total

# ---------- 取数 ----------
def _get_ts_codes(engine, pro) -> List[str]:
    try:
        with engine.connect() as c:
            rows = c.execute(text("SELECT DISTINCT ts_code_t FROM stock_basic")).fetchall()
        codes = [r[0] for r in rows if r and r[0]]
        if codes: return codes
    except Exception:
        pass
    df = pro.stock_basic(fields="ts_code")
    return [] if df is None or df.empty else df["ts_code"].dropna().astype(str).tolist()

def _fetch_one_code_period(pro, code: str, period: str) -> pd.DataFrame:
    ind = pro.fina_indicator(ts_code=code, end_date=period, fields=FIELDS_IND)
    if ind is None or ind.empty:
        ind = pd.DataFrame(columns=["ts_code","ann_date","f_ann_date","end_date","roe","roa","grossprofit_margin"])
    else:
        ind = ind.copy()
        if "gross_margin" not in ind.columns and "grossprofit_margin" in ind.columns:
            ind["gross_margin"] = ind["grossprofit_margin"]
        for c in ["ts_code","ann_date","f_ann_date","end_date","roe","roa","gross_margin"]:
            if c not in ind.columns: ind[c] = None
        ind = ind[["ts_code","ann_date","f_ann_date","end_date","roe","roa","gross_margin"]]

    inc = pro.income(ts_code=code, period=period, fields=FIELDS_INC)
    if inc is None or inc.empty:
        inc = pd.DataFrame(columns=["ts_code","ann_date","f_ann_date","end_date","total_revenue","operate_profit","n_income"])
    else:
        inc = inc.copy()
        for c in ["ts_code","ann_date","f_ann_date","end_date","total_revenue","operate_profit","n_income"]:
            if c not in inc.columns: inc[c] = None
        inc.rename(columns={"n_income":"net_profit"}, inplace=True)
        inc = inc[["ts_code","ann_date","f_ann_date","end_date","total_revenue","operate_profit","net_profit"]]

    bs  = pro.balancesheet(ts_code=code, period=period, fields=FIELDS_BS)
    if bs is None or bs.empty:
        bs = pd.DataFrame(columns=["ts_code","ann_date","f_ann_date","end_date","total_assets","total_liab"])
    else:
        bs = bs.copy()
        for c in ["ts_code","ann_date","f_ann_date","end_date","total_assets","total_liab"]:
            if c not in bs.columns: bs[c] = None
        bs = bs[["ts_code","ann_date","f_ann_date","end_date","total_assets","total_liab"]]

    if ind.empty and inc.empty and bs.empty:
        return pd.DataFrame()

    m = pd.merge(ind, inc, on=["ts_code","end_date"], how="outer", suffixes=("_ind","_inc"))
    m = pd.merge(m, bs, on=["ts_code","end_date"], how="outer")

    def _coalesce(*vals):
        for v in vals:
            if pd.notnull(v) and v not in ("", None): return v
        return None
    m["f_ann_date"] = m.apply(lambda r: _coalesce(r.get("f_ann_date_ind"), r.get("f_ann_date_inc"), r.get("f_ann_date")), axis=1)
    m["ann_date"]   = m.apply(lambda r: _coalesce(r.get("ann_date_ind"),   r.get("ann_date_inc"),   r.get("ann_date")),   axis=1)

    m["roe"]            = m.get("roe")
    m["roa"]            = m.get("roa")
    m["gross_margin"]   = m.get("gross_margin")
    m["total_revenue"]  = m.get("total_revenue")
    m["operate_profit"] = m.get("operate_profit")
    m["net_profit"]     = m.get("net_profit")
    m["total_assets"]   = m.get("total_assets")
    m["total_liab"]     = m.get("total_liab")
    m = m[["ts_code","ann_date","f_ann_date","end_date",
           "total_revenue","operate_profit","net_profit",
           "total_assets","total_liab","roe","roa","gross_margin"]].copy()
    return m

# ---------- 主流程 ----------
def main(start_period: Optional[str]=None, end_period: Optional[str]=None):
    pro = get_pro()
    eng = get_engine()

    last = get_max_date(eng, TABLE, "end_date_t")  # YYYYMMDD or None
    if start_period is None:
        start_period = _next_period(last) if last else "19900331"
    if end_period is None:
        end_period = _latest_period_until_today()

    # 构造期间列表（逐季）
    periods = []
    p = start_period
    while int(p) <= int(end_period):
        periods.append(p)
        p = _next_period(p)

    log.info(f"[increment] table={TABLE} periods={len(periods)} [{start_period}~{end_period}]")

    codes = _get_ts_codes(eng, pro)
    log.info(f"[increment] table={TABLE} codes={len(codes)}")

    total = 0
    for idx, code in enumerate(codes, 1):
        try:
            frames = []
            for p in periods:
                df = _fetch_one_code_period(pro, code, p)
                if not df.empty:
                    frames.append(df)
                time.sleep(0.05)
            if not frames:
                if idx % 200 == 0: log.info(f"[increment] {idx}/{len(codes)} {code} empty")
                time.sleep(0.05); continue
            raw = pd.concat(frames, ignore_index=True)
            out = _normalize_and_rename(raw)
            n = _upsert(eng, out)
            total += n
            if n or (idx % 100 == 0):
                log.info(f"[increment] {idx}/{len(codes)} {code} upsert rows={n} total={total}")
            time.sleep(0.1)
        except Exception as e:
            log.exception(f"[increment] {idx}/{len(codes)} {code} error: {e}")
            time.sleep(0.2)

    log.info(f"[increment] done table={TABLE} rows={total} periods={len(periods)}")

if __name__ == "__main__":
    main()
