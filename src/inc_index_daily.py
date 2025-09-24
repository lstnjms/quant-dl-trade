# -*- coding: utf-8 -*-
"""
批量增量下载：指数日线行情（TuShare index_daily）
- end 自动回退到最近开市日（周末/节假日免手改）
- 支持多个指数（逗号分隔），逐指数独立计算增量起点
- 安全拼接，避免 concat FutureWarning
最后更新: 2025-08-31
"""
import argparse, time, datetime as dt
import pandas as pd, numpy as np
from sqlalchemy import text
from utils import get_engine, get_pro, log, today_str

TABLE = "index_daily"
DATE_COL = "trade_date_t"
PKS = ("ts_code_t", "trade_date_t")
FIELDS = "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount"
DEFAULT_INIT_START = "20180101"

# --------------------- 小工具 ---------------------
def _to_date8(x):
    if x is None: return None
    try:
        if (isinstance(x, float) and np.isnan(x)) or pd.isna(x): return None
    except Exception:
        pass
    s = str(x).strip()
    if s == "" or s.lower() in ("none", "nat", "nan"): return None
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

def _safe_concat(buf):
    good = []
    for d in buf:
        try:
            if d is not None and isinstance(d, pd.DataFrame):
                d2 = d.dropna(how="all")
                if not d2.empty:
                    good.append(d2)
        except Exception:
            continue
    return pd.concat(good, ignore_index=True) if good else None

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
    ph   = ",".join(f":{c}" for c in cols)
    upd  = ",".join(f"`{c}`=VALUES(`{c}`)" for c in cols if c not in PKS)
    sql  = f"INSERT INTO `{TABLE}` ({colq}) VALUES ({ph}) ON DUPLICATE KEY UPDATE {upd}"

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
    """将 end 对齐到最近开市日：先本地 trade_cal，再 TuShare，最后按工作日兜底"""
    with eng.connect() as conn:
        row = conn.execute(
            text("SELECT MAX(cal_date_t) FROM trade_cal WHERE exchange_t='' AND is_open_t=1 AND cal_date_t<=:d"),
            {"d": dt.datetime.strptime(end_ymd, "%Y%m%d").date()}
        ).scalar()
    if row:
        v = pd.to_datetime(row).strftime("%Y%m%d")
        if v != end_ymd:
            log.info(f"[end-align] {end_ymd} -> {v} (last open day by local trade_cal)")
        return v
    # TuShare 回退 30 天
    try:
        start_win = (pd.to_datetime(end_ymd) - pd.Timedelta(days=30)).strftime("%Y%m%d")
        cal = pro.trade_cal(exchange='', start_date=start_win, end_date=end_ymd, is_open='1', fields='cal_date,is_open')
        if cal is not None and not cal.empty:
            v = cal['cal_date'].max()
            if v != end_ymd:
                log.info(f"[end-align] {end_ymd} -> {v} (last open day by TuShare)")
            return v
    except Exception as e:
        log.warning(f"[end-align] fetch trade_cal failed: {e}")
    # 工作日兜底
    d = pd.to_datetime(end_ymd)
    for _ in range(10):
        if d.weekday() < 5:
            v = d.strftime("%Y%m%d")
            if v != end_ymd:
                log.info(f"[end-align] {end_ymd} -> {v} (weekday fallback)")
            return v
        d -= pd.Timedelta(days=1)
    return end_ymd

def _get_max_date_by_code(eng, code: str):
    with eng.connect() as conn:
        row = conn.execute(
            text(f"SELECT MAX({DATE_COL}) FROM `{TABLE}` WHERE ts_code_t=:c"),
            {"c": code}
        ).scalar()
    return None if row is None else pd.to_datetime(row).strftime("%Y%m%d")

# --------------------- 主流程 ---------------------
def main(sleep_s: float, init_start: str, codes_arg: str, align_open_day: int):
    pro = get_pro(); eng = get_engine()
    end = today_str()
    if align_open_day:
        end = _last_open_day(end, eng, pro)

    # 解析指数代码
    codes = [c.strip() for c in (codes_arg or "").split(",") if c.strip()]
    if not codes:
        log.error("请通过 --code 指定至少一个指数代码，例如 000905.SH 或 000905.SH,000300.SH")
        print("[error] missing --code")
        return

    log.info(f"[codes] increment for {len(codes)} index code(s): {codes}")
    print(f"[start] inc_index_daily end={end} codes={codes}")

    total = 0
    for i, code in enumerate(codes, 1):
        last = _get_max_date_by_code(eng, code)
        start = _next_day(last) if last else (init_start or DEFAULT_INIT_START)
        if start > end:
            log.info(f"[inc-index] {code} up-to-date: last={last}, end={end}")
            continue

        log.info(f"[inc-index] {code} range={start}~{end}")
        buf = []
        try:
            df = pro.index_daily(ts_code=code, start_date=start, end_date=end, fields=FIELDS)
            if df is not None and not df.empty:
                buf.append(df)
        except Exception as ex:
            log.warning(f"[inc-index] fetch {code} error: {ex}")

        cat = _safe_concat(buf)
        if cat is not None:
            n = _upsert(eng, _normalize(cat)); total += n
            log.info(f"[inc-index] {code} rows={n} total={total}")
        time.sleep(sleep_s)

    log.info(f"[inc-index] done table={TABLE} rows={total} end={end}")
    print(f"[done] rows={total} end={end}")

# --------------------- 入口 ---------------------
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sleep", type=float, default=0.02, help="每指数限流间隔秒")
    ap.add_argument("--init-start", default=DEFAULT_INIT_START, help="库空默认起点(YYYYMMDD)")
    ap.add_argument("--code", type=str, required=True,
                    help="指数代码，单个或逗号分隔多个，如 000905.SH 或 000905.SH,000300.SH")
    ap.add_argument("--align-open-day", type=int, default=1, help="end是否对齐最近开市日，默认1")
    args = ap.parse_args()
    main(args.sleep, args.init_start, args.code.strip(), args.align_open_day)
