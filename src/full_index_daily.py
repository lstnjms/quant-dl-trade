# -*- coding: utf-8 -*-
"""
批量全量下载：指数日线行情（TuShare index_daily）
- 一天仅一条记录（指数本身的开高低收、涨跌幅、量额）
- 支持一次指定一个或多个指数代码（逗号分隔）
用法示例：
    python full_index_daily.py --start 20180101 --end 20250815 --code 000905.SH
    python full_index_daily.py --start 20180101 --end 20250815 --code 000905.SH,000300.SH
"""
import argparse, time
import pandas as pd, numpy as np
from sqlalchemy import text
from utils import get_engine, get_pro, log, today_str, delete_range

TABLE = "index_daily"
DATE_COL = "trade_date_t"
PKS = ("ts_code_t", "trade_date_t")
FIELDS = "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount"
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
    """字段重命名 + 数值清洗"""
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

    # 数值列统一 to_numeric + 非有限值置空
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
            conn.execute(text(sql), chunk.to_dict(orient="records"))
            total += chunk.shape[0]
    return total

def main(start: str, end: str, sleep_s: float, codes_arg: str):
    pro = get_pro(); eng = get_engine()

    # 预处理指数代码列表
    codes = []
    if codes_arg:
        codes = [c.strip() for c in codes_arg.split(",") if c.strip()]
    if not codes:
        log.error("请通过 --code 指定至少一个指数代码，例如 000905.SH")
        return
    log.info(f"[codes] downloading {len(codes)} index code(s): {codes}")

    # 先删再写（幂等）
    fmt = lambda ymd: f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"
    delete_range(eng, TABLE, DATE_COL, fmt(start), fmt(end))

    total, buf = 0, []
    for i, code in enumerate(codes, 1):
        try:
            df = pro.index_daily(ts_code=code, start_date=start, end_date=end, fields=FIELDS)
            if df is not None and not df.empty:
                buf.append(df)
            if i % 50 == 0 and buf:
                n = _upsert(eng, _normalize(pd.concat(buf, ignore_index=True))); total += n; buf.clear()
                log.info(f"[full-index] {i}/{len(codes)} rows={n} total={total}")
            time.sleep(sleep_s)
        except Exception as ex:
            log.warning(f"[full-index] {i}/{len(codes)} {code} error: {ex}")
            time.sleep(sleep_s*2)

    if buf:
        n = _upsert(eng, _normalize(pd.concat(buf, ignore_index=True))); total += n
        log.info(f"[full-index] final rows={n} total={total}")

    log.info(f"[full-index] done table={TABLE} rows={total} range={start}~{end}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=DEFAULT_INIT_START, help="起始日期 YYYYMMDD")
    ap.add_argument("--end",   default=today_str(), help="结束日期 YYYYMMDD")
    ap.add_argument("--sleep", type=float, default=0.02, help="每代码限流间隔秒")
    ap.add_argument("--code",  type=str, default="", help="指数代码，单个或逗号分隔多个，如 000905.SH 或 000905.SH,000300.SH")
    args = ap.parse_args()
    main(args.start.strip(), args.end.strip(), args.sleep, args.code.strip())
