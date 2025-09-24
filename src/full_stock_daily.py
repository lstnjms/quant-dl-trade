# -*- coding: utf-8 -*-
"""
批量全量下载：A股日线行情（TuShare stock_daily）
- 内置获取全市场或指定指数成分股
- 按“股票代码”分批下载 start~end 区间
"""
import argparse, time
import pandas as pd, numpy as np
from sqlalchemy import text
from utils import get_engine, get_pro, log, today_str, delete_range

TABLE = "stock_daily"
DATE_COL = "trade_date_t"
PKS = ("ts_code_t", "trade_date_t")
FIELDS = (
    "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount"
)
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

def main(start: str, end: str, sleep_s: float, index_code: str):
    pro = get_pro(); eng = get_engine()

    # 删除原区间数据
    fmt = lambda ymd: f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"
    delete_range(eng, TABLE, DATE_COL, fmt(start), fmt(end))

    # 获取股票代码（全市场或指数成分股）
    try:
        if index_code:
            df_members = pro.index_weight(index_code=index_code, start_date=start, end_date=end, fields="con_code")
            codes = [] if df_members is None or df_members.empty else df_members["con_code"].drop_duplicates().tolist()
        else:
            code_df = pro.stock_basic(exchange="", list_status="L", fields="ts_code")
            codes = [] if code_df is None or code_df.empty else code_df["ts_code"].astype(str).tolist()
    except Exception as e:
        log.error(f"[codes] fetch codes failed: {e}")
        codes = []

    log.info(f"[codes] using {len(codes)} codes from {'index '+index_code if index_code else 'full market'}")
    total, buf = 0, []

    for i, code in enumerate(codes, 1):
        try:
            df = pro.daily(ts_code=code, start_date=start, end_date=end, fields=FIELDS)
            if df is not None and not df.empty:
                buf.append(df)
            if i % 100 == 0 and buf:
                n = _upsert(eng, _normalize(pd.concat(buf, ignore_index=True))); total += n; buf.clear()
                log.info(f"[full-batch] {i}/{len(codes)} rows={n} total={total}")
            time.sleep(sleep_s)
        except Exception as ex:
            log.warning(f"[full-batch] {i}/{len(codes)} {code} error: {ex}")
            time.sleep(sleep_s*2)

    if buf:
        n = _upsert(eng, _normalize(pd.concat(buf, ignore_index=True))); total += n
        log.info(f"[full-batch] final rows={n} total={total}")

    log.info(f"[full-batch] done table={TABLE} rows={total} range={start}~{end}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=DEFAULT_INIT_START, help="YYYYMMDD")
    ap.add_argument("--end",   default=today_str(), help="YYYYMMDD")
    ap.add_argument("--sleep", type=float, default=0.02, help="每代码限流间隔秒")
    ap.add_argument("--index", type=str, default="", help="指数代码(如 000905.SH 表示中证500)")
    args = ap.parse_args()
    main(args.start.strip(), args.end.strip(), args.sleep, args.index.strip())
