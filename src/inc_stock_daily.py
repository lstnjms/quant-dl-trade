# -*- coding: utf-8 -*-
"""
批量增量下载：A股日线行情（TuShare daily）
- 自动对齐 end 至最近开市日；股票池三层兜底
最后更新: 2025-08-31
"""
import argparse, sys, time, datetime as dt
import pandas as pd, numpy as np
from sqlalchemy import text
from utils import get_engine, get_pro, log, today_str, get_max_date

TABLE = "stock_daily"
DATE_COL = "trade_date_t"
PKS = ("ts_code_t", "trade_date_t")
FIELDS = ("ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount")
DEFAULT_INIT_START = "20180101"

def _to_date8(x):
    if x is None: return None
    try:
        if (isinstance(x, float) and np.isnan(x)) or pd.isna(x): return None
    except Exception: pass
    s = str(x).strip()
    if s == "" or s.lower() in ("none","nat","nan"): return None
    d = "".join(ch for ch in s if ch.isdigit())
    if len(d) < 8: return None
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}"

def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=[
            "ts_code_t","trade_date_t","open_t","high_t","low_t","close_t",
            "pre_close_t","change_t","pct_chg_t","vol_t","amount_t"
        ])
    df = df.copy()
    if "trade_date" in df.columns:
        df["trade_date"] = df["trade_date"].apply(_to_date8)
    df.rename(columns={
        "ts_code":"ts_code_t","trade_date":"trade_date_t",
        "open":"open_t","high":"high_t","low":"low_t","close":"close_t",
        "pre_close":"pre_close_t","change":"change_t","pct_chg":"pct_chg_t",
        "vol":"vol_t","amount":"amount_t"
    }, inplace=True)
    num_cols = [c for c in df.columns if c.endswith("_t") and c not in ("ts_code_t","trade_date_t")]
    for c in num_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")
        df.loc[~np.isfinite(df[c]), c] = None
    cols = [
        "ts_code_t","trade_date_t","open_t","high_t","low_t","close_t",
        "pre_close_t","change_t","pct_chg_t","vol_t","amount_t"
    ]
    for c in cols:
        if c not in df.columns: df[c] = None
    return df[cols]

def _upsert(engine, df: pd.DataFrame) -> int:
    if df is None or df.empty: return 0
    cols = [
        "ts_code_t","trade_date_t","open_t","high_t","low_t","close_t",
        "pre_close_t","change_t","pct_chg_t","vol_t","amount_t"
    ]
    df = df[cols].copy()
    df.replace({pd.NA: None, np.nan: None, np.inf: None, -np.inf: None}, inplace=True)
    df = df.astype(object)
    df.drop_duplicates(subset=list(PKS), keep="last", inplace=True)
    colq = ",".join(f"`{c}`" for c in cols)
    ph = ",".join(f":{c}" for c in cols)
    upd = ",".join(f"`{c}`=VALUES(`{c}`)" for c in cols if c not in PKS)
    sql = f"INSERT INTO `{TABLE}` ({colq}) VALUES ({ph}) ON DUPLICATE KEY UPDATE {upd}"
    total = 0
    with engine.begin() as conn:
        for i in range(0, len(df), 10000):
            chunk = df.iloc[i:i+10000].copy()
            chunk.replace({pd.NA: None, np.nan: None, np.inf: None, -np.inf: None}, inplace=True)
            conn.execute(text(sql), chunk.to_dict(orient="records"))
            total += chunk.shape[0]
    return total

def _next_day(ymd: str) -> str:
    d = dt.datetime.strptime(ymd, "%Y%m%d").date()
    return (d + dt.timedelta(days=1)).strftime("%Y%m%d")

def _last_open_day(end_ymd: str, eng, pro) -> str:
    with eng.connect() as conn:
        row = conn.execute(
            text("SELECT MAX(cal_date_t) FROM trade_cal WHERE exchange_t='' AND is_open_t=1 AND cal_date_t<=:d"),
            {"d": dt.datetime.strptime(end_ymd, "%Y%m%d").date()}
        ).scalar()
    if row:
        v = pd.to_datetime(row).strftime("%Y%m%d")
        if v != end_ymd: log.info(f"[end-align] {end_ymd} -> {v} (local)")
        return v
    try:
        start_win = (pd.to_datetime(end_ymd) - pd.Timedelta(days=30)).strftime("%Y%m%d")
        cal = pro.trade_cal(exchange='', start_date=start_win, end_date=end_ymd, is_open='1', fields='cal_date,is_open')
        if cal is not None and not cal.empty:
            v = cal['cal_date'].max()
            if v != end_ymd: log.info(f"[end-align] {end_ymd} -> {v} (pro)")
            return v
    except Exception as e:
        log.warning(f"[end-align] trade_cal failed: {e}")
    d = pd.to_datetime(end_ymd)
    for _ in range(10):
        if d.weekday() < 5: return d.strftime("%Y%m%d")
        d -= pd.Timedelta(days=1)
    return end_ymd

def _snapshot_codes(pro, index_code: str, end_ymd: str):
    for back in [0,1,2,3,4,5,10,20,60]:
        snap = (pd.to_datetime(end_ymd) - pd.Timedelta(days=back)).strftime("%Y%m%d")
        dfm = pro.index_weight(index_code=index_code, start_date=snap, end_date=snap, fields="con_code")
        if dfm is not None and not dfm.empty:
            return dfm["con_code"].drop_duplicates().tolist(), snap
    return [], None

def _get_codes(eng, pro, end_ymd: str, index_code: str):
    try:
        with eng.connect() as conn:
            rows = conn.execute(text("SELECT DISTINCT con_code_t FROM index_weight WHERE con_code_t<>''")).fetchall()
        codes = [r[0] for r in rows]
        if codes:
            log.info(f"[codes] using {len(codes)} codes from local index_weight pool")
            return codes
    except Exception as e:
        log.warning(f"[codes] local pool failed: {e}")
    if index_code:
        codes, snap = _snapshot_codes(pro, index_code, end_ymd)
        if codes:
            log.info(f"[codes] using {len(codes)} codes from index {index_code} @ {snap}")
            return codes
    try:
        code_df = pro.stock_basic(exchange="", list_status="L", fields="ts_code")
        codes = [] if code_df is None or code_df.empty else code_df["ts_code"].astype(str).tolist()
        log.info(f"[codes] using {len(codes)} codes from full market fallback")
        return codes
    except Exception as e:
        log.error(f"[codes] fetch fallback failed: {e}")
        return []

def _safe_concat(buf):
    good = []
    for d in buf:
        try:
            if d is not None and isinstance(d, pd.DataFrame) and not d.empty and not d.dropna(how="all").empty:
                good.append(d)
        except Exception:
            continue
    return pd.concat(good, ignore_index=True) if good else None

def main(sleep_s: float, init_start: str, index_code: str, align_open_day: int):
    pro = get_pro(); eng = get_engine()
    end = today_str()
    if align_open_day:
        end = _last_open_day(end, eng, pro)
    last = get_max_date(eng, TABLE, DATE_COL)
    start = _next_day(last) if last else (init_start or DEFAULT_INIT_START)
    if start > end:
        log.info(f"[inc] up-to-date: last={last}, end={end}")
        return

    codes = _get_codes(eng, pro, end, index_code)
    total, buf = 0, []
    for i, code in enumerate(codes, 1):
        try:
            df = pro.daily(ts_code=code, start_date=start, end_date=end, fields=FIELDS)
            if df is not None and not df.empty:
                buf.append(df)
            if i % 100 == 0:
                cat = _safe_concat(buf); buf.clear()
                if cat is not None:
                    n = _upsert(eng, _normalize(cat)); total += n
                    log.info(f"[inc-batch] {i}/{len(codes)} rows={n} total={total}")
            time.sleep(sleep_s)
        except Exception as ex:
            log.warning(f"[inc-batch] {i}/{len(codes)} {code} error: {ex}")
            time.sleep(sleep_s*2)

    cat = _safe_concat(buf)
    if cat is not None:
        n = _upsert(eng, _normalize(cat)); total += n
        log.info(f"[inc-batch] final rows={n} total={total}")
    log.info(f"[inc-batch] done table={TABLE} rows={total} range={start}~{end}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sleep", type=float, default=0.02, help="每代码限流间隔秒")
    ap.add_argument("--init-start", default=DEFAULT_INIT_START, help="库空默认起点(YYYYMMDD)")
    ap.add_argument("--index", type=str, default="", help="指数代码(如 000905.SH 表示中证500)")
    ap.add_argument("--align-open-day", type=int, default=1, help="end是否对齐最近开市日，默认1")
    args = ap.parse_args()
    main(args.sleep, args.init_start, args.index.strip(), args.align_open_day)
