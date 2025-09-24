# -*- coding: utf-8 -*-
"""
增量下载：同花顺板块指数行情（TuShare ths_daily）
- 从库内 MAX(trade_date_t) 的下一天起，拉到今天
- 字段 *_t 映射；强清洗；批量 UPSERT 并二分定位坏行；递归拆窗（3000上限）
- 代码来源：优先库表 ths_index；否则 pro.ths_index；也可 --codes 指定

用法示例：
  python inc_ths_daily.py
  python inc_ths_daily.py --init-start 20180101 --qpm 480
  python inc_ths_daily.py --codes 885001.TI,885008.TI --qpm 480
"""
import argparse, time, datetime as dt
from typing import List

import numpy as np
import pandas as pd
from sqlalchemy import text

from utils import get_engine, get_pro, log, today_str, get_max_date

TABLE = "ths_daily"
DATE_COL = "trade_date_t"
PKS = ("ts_code_t", "trade_date_t")

# TuShare ths_daily 字段（14个）
FIELDS = (
    "ts_code,trade_date,open,high,low,close,pre_close,avg_price,change,"
    "pct_change,vol,turnover_rate,total_mv,float_mv"
)

DEFAULT_INIT_START = "20180101"

# ------------------------ 工具函数 ------------------------ #

class RateLimiter:
    """按 QPM 均匀限速"""
    def __init__(self, qpm: float = 480.0):
        self.min_interval = 60.0 / max(1.0, qpm)
        self._t = 0.0
    def wait(self):
        now = time.perf_counter()
        dt = now - self._t
        if dt < self.min_interval:
            time.sleep(self.min_interval - dt)
        self._t = time.perf_counter()

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

def _next_day(ymd: str) -> str:
    d = dt.datetime.strptime(ymd, "%Y%m%d").date()
    return (d + dt.timedelta(days=1)).strftime("%Y%m%d")

def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """重命名 + 数值清洗 + 极值裁剪 + 主键校验"""
    if df is None or df.empty:
        return pd.DataFrame(columns=[
            "ts_code_t","trade_date_t","open_t","high_t","low_t","close_t",
            "pre_close_t","avg_price_t","change_t","pct_change_t",
            "vol_t","turnover_rate_t","total_mv_t","float_mv_t",
        ])
    df = df.copy()
    if "trade_date" in df.columns:
        df["trade_date"] = df["trade_date"].apply(_to_date8)
    df.rename(columns={
        "ts_code":"ts_code_t",
        "trade_date":"trade_date_t",
        "open":"open_t",
        "high":"high_t",
        "low":"low_t",
        "close":"close_t",
        "pre_close":"pre_close_t",
        "avg_price":"avg_price_t",
        "change":"change_t",
        "pct_change":"pct_change_t",
        "vol":"vol_t",
        "turnover_rate":"turnover_rate_t",
        "total_mv":"total_mv_t",
        "float_mv":"float_mv_t",
    }, inplace=True)

    # 数值统一 to_numeric
    num_cols = [c for c in [
        "open_t","high_t","low_t","close_t","pre_close_t","avg_price_t",
        "change_t","pct_change_t","vol_t","turnover_rate_t","total_mv_t","float_mv_t"
    ] if c in df.columns]
    for c in num_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    # 保险丝：极值裁剪（防 decimal 溢出 & 脏数据）
    clip = lambda s, lo, hi: s.where(s.between(lo, hi))
    caps = {
        "open_t": (0, 1e7), "high_t": (0, 1e7), "low_t": (0, 1e7),
        "close_t": (0, 1e7), "pre_close_t": (0, 1e7), "avg_price_t": (0, 1e7),
        "change_t": (-1e7, 1e7), "pct_change_t": (-1000, 1000),
        "vol_t": (0, 1e14),
        "turnover_rate_t": (0, 100000),
        "total_mv_t": (0, 1e16), "float_mv_t": (0, 1e16),
    }
    for c, (lo, hi) in caps.items():
        if c in df.columns:
            df[c] = clip(df[c], lo, hi)

    # 丢弃主键缺失
    df.dropna(subset=["ts_code_t","trade_date_t"], inplace=True)

    cols = [
        "ts_code_t","trade_date_t","open_t","high_t","low_t","close_t",
        "pre_close_t","avg_price_t","change_t","pct_change_t",
        "vol_t","turnover_rate_t","total_mv_t","float_mv_t",
    ]
    for c in cols:
        if c not in df.columns: df[c] = None
    return df[cols]

def _upsert_binary_split(engine, df: pd.DataFrame, bad_csv: str) -> int:
    """批量入库，失败则二分定位坏行到 CSV"""
    if df is None or df.empty: return 0
    cols = [
        "ts_code_t","trade_date_t","open_t","high_t","low_t","close_t",
        "pre_close_t","avg_price_t","change_t","pct_change_t",
        "vol_t","turnover_rate_t","total_mv_t","float_mv_t",
    ]
    df = df[cols].copy()
    df.replace({pd.NA: None, np.nan: None, np.inf: None, -np.inf: None}, inplace=True)
    df = df.astype(object)
    df.drop_duplicates(subset=list(PKS), keep="last", inplace=True)

    colq = ",".join(f"`{c}`" for c in cols)
    ph   = ",".join(f":{c}" for c in cols)
    upd  = ",".join(f"`{c}`=VALUES(`{c}`)" for c in cols if c not in PKS)
    sql  = f"INSERT INTO `{TABLE}` ({colq}) VALUES ({ph}) ON DUPLICATE KEY UPDATE {upd}"

    def exec_batch(batch: pd.DataFrame) -> int:
        try:
            with engine.begin() as conn:
                conn.execute(text(sql), batch.to_dict(orient="records"))
            return len(batch)
        except Exception as e:
            if len(batch) == 1:
                row = batch.iloc[0:1].copy()
                try:
                    row.assign(error=str(e)).to_csv(
                        bad_csv, mode="a", index=False,
                        header=not pd.io.common.file_exists(bad_csv)
                    )
                except Exception:
                    pass
                log.warning(f"[upsert-skip-1] {row.iloc[0].get('ts_code_t')} {row.iloc[0].get('trade_date_t')} err: {e}")
                return 0
            mid = len(batch)//2
            return exec_batch(batch.iloc[:mid]) + exec_batch(batch.iloc[mid:])

    total = 0
    i = 0
    while i < len(df):
        chunk = df.iloc[i:i+10000].copy()
        total += exec_batch(chunk)
        i += 10000
    return total

def _fetch_window(pro, ts_code: str, s: str, e: str) -> pd.DataFrame:
    """直接调用接口拉一个窗口"""
    return pro.ths_daily(ts_code=ts_code, start_date=s, end_date=e, fields=FIELDS)

def _date_mid(s: str, e: str) -> str:
    d1 = dt.datetime.strptime(s, "%Y%m%d").date()
    d2 = dt.datetime.strptime(e, "%Y%m%d").date()
    mid = d1 + (d2 - d1) // 2
    return mid.strftime("%Y%m%d")

def _safe_fetch_recursive(pro, ts_code: str, s: str, e: str, max_rows: int, min_days: int, limiter: RateLimiter,
                          attempt: int = 0) -> pd.DataFrame:
    """
    递归拉取：若数据量触顶/异常则拆半窗口
    - max_rows: 单次 API 允许返回的最大行数（一般 3000）
    - min_days: 最小日期窗口（天）
    """
    limiter.wait()
    try:
        df = _fetch_window(pro, ts_code, s, e)
    except Exception as ex:
        if s == e:
            raise
        mid = _date_mid(s, e)
        if mid <= s or mid >= e:
            raise
        left = _safe_fetch_recursive(pro, ts_code, s, mid, max_rows, min_days, limiter, attempt+1)
        right = _safe_fetch_recursive(pro, ts_code, mid, e, max_rows, min_days, limiter, attempt+1)
        return pd.concat([left, right], ignore_index=True)

    if df is None or df.empty or len(df) < max_rows:
        return df if df is not None else pd.DataFrame()

    if s == e:
        return df
    mid = _date_mid(s, e)
    if mid <= s or mid >= e:
        return df
    left = _safe_fetch_recursive(pro, ts_code, s, mid, max_rows, min_days, limiter, attempt+1)
    right = _safe_fetch_recursive(pro, ts_code, mid, e, max_rows, min_days, limiter, attempt+1)
    return pd.concat([left, right], ignore_index=True)

def _get_codes_from_db(engine) -> List[str]:
    """优先从库里 ths_index 取代码（若存在该表）"""
    try:
        sql = "SELECT DISTINCT `ts_code_t` FROM `ths_index`"
        with engine.begin() as conn:
            rows = conn.execute(text(sql)).fetchall()
        codes = [r[0] for r in rows if r and r[0]]
        return sorted(set(codes))
    except Exception:
        return []

def _get_codes_from_api(pro) -> List[str]:
    """退而求其次：调用 ths_index 获取板块代码"""
    try:
        df = pro.ths_index(fields="ts_code,name,exchange,type")
        if df is None or df.empty: return []
        return sorted(set(df["ts_code"].dropna().astype(str).tolist()))
    except Exception:
        return []

def _resolve_codes(codes_arg: str, engine, pro) -> List[str]:
    if codes_arg:
        codes = [c.strip() for c in codes_arg.split(",") if c.strip()]
        log.info(f"[codes] from args: {len(codes)}")
        return codes
    codes = _get_codes_from_db(engine)
    if codes:
        log.info(f"[codes] from DB ths_index: {len(codes)}")
        return codes
    codes = _get_codes_from_api(pro)
    log.info(f"[codes] from API ths_index: {len(codes)}")
    return codes

# ------------------------ 主流程 ------------------------ #

def main(init_start: str, qpm: float, max_rows: int, min_chunk_days: int,
         codes_arg: str, bad_csv: str):
    pro = get_pro()
    eng = get_engine()
    limiter = RateLimiter(qpm=qpm)

    end = today_str()
    # 计算增量起点：库内最大交易日 + 1 天；库空用 init_start
    last = get_max_date(eng, TABLE, DATE_COL)   # 返回 YYYYMMDD 或 None
    start = _next_day(last) if last else (init_start or DEFAULT_INIT_START)

    # 板块代码列表
    codes = _resolve_codes(codes_arg, eng, pro)
    if not codes:
        log.error("[ths_daily-inc] no ts_code to fetch. abort.")
        return

    log.info(f"[ths_daily-inc] codes={len(codes)} qpm={qpm} max_rows={max_rows} min_chunk_days={min_chunk_days} range={start}~{end}")

    total = 0
    for i, code in enumerate(codes, 1):
        # 指数退避重试 3 次（窗口级）
        for attempt in range(3):
            try:
                raw = _safe_fetch_recursive(pro, code, start, end, max_rows, min_chunk_days, limiter)
                if raw is None or raw.empty:
                    if i % 20 == 0:
                        log.info(f"[inc-ths] {i}/{len(codes)} {code} empty")
                    break
                n = _upsert_binary_split(eng, _normalize(raw), bad_csv=bad_csv)
                total += n
                log.info(f"[inc-ths] {i}/{len(codes)} {code} rows={n} total={total}")
                break
            except Exception as ex:
                log.warning(f"[inc-ths] {i}/{len(codes)} {code} attempt {attempt+1}/3 error: {ex}")
                time.sleep(0.25 * (2 ** attempt))
        else:
            log.error(f"[inc-ths] {i}/{len(codes)} {code} failed after retries")
    log.info(f"[inc-ths] done table={TABLE} rows={total} range={start}~{end} bad_csv={bad_csv}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--init-start", default=DEFAULT_INIT_START, help="库空默认起点 YYYYMMDD; default=20180101")
    ap.add_argument("--qpm",   type=float, default=480,   help="每分钟最大请求数（建议≤500）")
    ap.add_argument("--max-rows", type=int, default=3000, help="单次接口最大行数（一般 3000）触顶即拆窗")
    ap.add_argument("--min-chunk-days", type=int, default=1, help="最小拆窗天数（保持 1 天用于兜底）")
    ap.add_argument("--codes", default="", help="逗号分隔的板块 ts_code 列表；留空=自动获取")
    ap.add_argument("--bad-csv", default="bad_ths_daily_inc_bad.csv", help="坏数据沉淀CSV路径")
    args = ap.parse_args()

    main(
        args.init_start.strip(),
        args.qpm,
        args.max_rows,
        args.min_chunk_days,
        args.codes.strip(),
        args.bad_csv.strip(),
    )
