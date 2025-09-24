# -*- coding: utf-8 -*-
import argparse, os, time
from typing import List, Dict

import numpy as np
import pandas as pd
import tushare as ts
from sqlalchemy import text

from config import TUSHARE_TOKEN
from utils import get_engine, get_pro as utils_get_pro, log, today_str, get_max_date

TABLE   = "stk_nineturn"
DATECOL = "trade_date_t"
PKS     = ("ts_code_t", "trade_date_t")

DEFAULT_INIT = "20180101"
DEFAULT_QPM  = 480.0
BADCSV       = "bad_nineturn_inc.csv"

def _get_pro_safe():
    try:
        pro = utils_get_pro()
        pro.query("stock_basic", limit=1)
        return pro
    except Exception as e:
        log.warning(f"[pro-fallback] utils.get_pro failed: {e}")
        if not TUSHARE_TOKEN:
            raise RuntimeError("TUSHARE_TOKEN 未配置，且 utils.get_pro() 不可用")
        ts.set_token(TUSHARE_TOKEN)
        return ts.pro_api(TUSHARE_TOKEN)

def _whoami(pro):
    try:
        u = pro.user(token=ts.get_token())
        log.info(f"[whoami] tushare id={u.get('uid')} name={u.get('nickname')} point={u.get('points')}")
    except Exception as e:
        log.warning(f"[whoami] failed: {e}")

class RateLimiter:
    def __init__(self, qpm: float):
        self.min_interval = 60.0 / max(1.0, qpm); self.tlast = 0.0
    def wait(self):
        now = time.perf_counter(); d = now - self.tlast
        if d < self.min_interval: time.sleep(self.min_interval - d)
        self.tlast = time.perf_counter()

def _to_date_only(x):
    if x is None: return None
    s = str(x).strip()
    if not s or s.lower() in ("none","nan","nat"): return None
    if len(s) >= 10 and s[4] == "-" and s[7] == "-": return s[:10]
    if len(s) == 8 and s.isdigit(): return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    try: return pd.to_datetime(s).strftime("%Y-%m-%d")
    except Exception: return None

MAP_CHOICES: Dict[str, list] = {
    "ts_code_t"        : ["ts_code"],
    "trade_date_t"     : ["trade_date"],
    "freq_t"           : ["freq"],
    "open_t"           : ["open"],
    "high_t"           : ["high"],
    "low_t"            : ["low"],
    "close_t"          : ["close"],
    "vol_t"            : ["vol"],
    "amount_t"         : ["amount"],
    "up_count_t"       : ["up_count","up_cnt","up"],
    "down_count_t"     : ["down_count","down_cnt","down"],
    "nine_up_turn_t"   : ["nine_up_turn","nine_up","n_up"],
    "nine_down_turn_t" : ["nine_down_turn","nine_down","n_down"],
}
OUT_COLS = list(MAP_CHOICES.keys())

def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=OUT_COLS)
    df = df.copy()
    if "trade_date" in df.columns:
        df["trade_date"] = df["trade_date"].apply(_to_date_only)
    out = pd.DataFrame()
    for oc, cs in MAP_CHOICES.items():
        for c in cs:
            if c in df.columns:
                out[oc] = df[c]; break
        if oc not in out.columns:
            out[oc] = None
    for c in ["open_t","high_t","low_t","close_t","vol_t","amount_t","up_count_t","down_count_t"]:
        out[c] = pd.to_numeric(out[c], errors="coerce")
    out.dropna(subset=["ts_code_t","trade_date_t"], inplace=True)
    out = out[OUT_COLS].drop_duplicates(subset=list(PKS), keep="last")
    return out

def _upsert_binary(engine, df: pd.DataFrame, bad_csv: str) -> int:
    if df is None or df.empty: return 0
    df = df[OUT_COLS].copy().astype(object)
    df.replace({pd.NA: None, np.nan: None, np.inf: None, -np.inf: None}, inplace=True)
    df.drop_duplicates(subset=list(PKS), keep="last", inplace=True)

    cols = OUT_COLS
    colq = ",".join(f"`{c}`" for c in cols)
    ph   = ",".join(f":{c}" for c in cols)
    upd  = ",".join(f"`{c}`=VALUES(`{c}`)" for c in cols if c not in PKS)
    sql  = f"INSERT INTO `{TABLE}` ({colq}) VALUES ({ph}) ON DUPLICATE KEY UPDATE {upd}"

    def exec_batch(b: pd.DataFrame) -> int:
        try:
            with engine.begin() as conn:
                conn.execute(text(sql), b.to_dict("records"))
            return len(b)
        except Exception as e:
            if len(b) == 1:
                row = b.iloc[0:1].copy()
                try:
                    header = not os.path.exists(bad_csv)
                    row.assign(error=str(e)).to_csv(bad_csv, mode="a", index=False, header=header)
                except Exception: pass
                log.warning(f"[upsert-skip-1] {row.iloc[0].get('ts_code_t')} {row.iloc[0].get('trade_date_t')} err: {e}")
                return 0
            mid = len(b)//2
            return exec_batch(b.iloc[:mid]) + exec_batch(b.iloc[mid:])

    total, i = 0, 0
    while i < len(df):
        total += exec_batch(df.iloc[i:i+10000].copy())
        i += 10000
    return total

def _codes_from_index(pro, index_code: str, end_ymd: str) -> List[str]:
    try:
        df = pro.index_weight(index_code=index_code, trade_date=end_ymd)
        if df is None or df.empty:
            start = (pd.to_datetime(end_ymd) - pd.Timedelta(days=30)).strftime("%Y%m%d")
            df = pro.index_weight(index_code=index_code, start_date=start, end_date=end_ymd)
        if df is None or df.empty:
            return []
        if "con_code" in df.columns:
            return sorted(set(df["con_code"].dropna().astype(str)))
        return sorted(set(df["ts_code"].dropna().astype(str)))
    except Exception as e:
        log.warning(f"[index_weight] {index_code} err: {e}")
        return []

def _codes_from_db(engine) -> List[str]:
    try:
        with engine.begin() as c:
            rows = c.execute(text(
                "SELECT `ts_code_t` FROM `stock_basic` WHERE `list_status_t`='L'"
            )).fetchall()
        return sorted({r[0] for r in rows if r and r[0]})
    except Exception:
        return []

def _codes_from_api(pro) -> List[str]:
    try:
        df = pro.stock_basic(fields="ts_code,list_status")
        if df is None or df.empty: return []
        return sorted(df.loc[df["list_status"]=="L","ts_code"].astype(str))
    except Exception:
        return []

def main(init_start: str, index: str, qpm: float):
    pro = _get_pro_safe(); eng = get_engine(); limiter = RateLimiter(qpm)
    _whoami(pro)

    # 预检
    try:
        pro.query("stk_nineturn", ts_code="000001.SZ", start_date="20240101", end_date="20240105", limit=1)
    except Exception as ex:
        log.error(f"[preflight] stk_nineturn not available: {ex}")
        return

    end   = today_str()
    last  = get_max_date(eng, TABLE, DATECOL)  # 可能是 'YYYY-MM-DD'
    start = (pd.to_datetime(last).to_pydatetime() + pd.Timedelta(days=1)).strftime("%Y%m%d") if last else init_start
    log.info(f"[inc] range={start}~{end}")

    codes = []
    if index:
        codes = _codes_from_index(pro, index, end)
        if not codes:
            log.warning(f"[codes] index {index} empty, fallback DB/API")
    if not codes:
        codes = _codes_from_db(eng) or _codes_from_api(pro)
    log.info(f"[codes] total={len(codes)} source={'index' if index else 'all'}")

    total = 0
    for i, code in enumerate(codes, 1):
        limiter.wait()
        try:
            df = pro.query("stk_nineturn", ts_code=code, start_date=start, end_date=end)
            if df is None or df.empty:
                if i % 100 == 0: log.info(f"[inc] {i}/{len(codes)} {code} empty")
                continue
            n = _upsert_binary(eng, _normalize(df), BADCSV)
            total += n
            if i % 20 == 0 or n > 0:
                log.info(f"[inc] {i}/{len(codes)} {code} rows={n} total={total}")
        except Exception as e:
            log.warning(f"[inc] {i}/{len(codes)} {code} err: {e}")

    log.info(f"[inc] done table={TABLE} rows={total} range={start}~{end} index={index or 'ALL'}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--init-start", default=DEFAULT_INIT)
    ap.add_argument("--index", default="", help="指数代码(如 000905.SH)")
    ap.add_argument("--qpm", type=float, default=DEFAULT_QPM)
    args = ap.parse_args()
    main(args.init_start.strip(), args.index.strip(), args.qpm)
