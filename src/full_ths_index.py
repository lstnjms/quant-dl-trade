# -*- coding: utf-8 -*-
"""全量：同花顺概念与行业指数（ths_index）
- 文档：https://tushare.pro/document/2?doc_id=259
- 策略：按 type 分批抓取（N/I/R/S/ST/TH/BB），先删全表，再 UPSERT
"""
import argparse, time
import numpy as np, pandas as pd
from sqlalchemy import text
from utils import get_engine, get_pro, log

TABLE = "ths_index"
FIELDS = "ts_code,name,count,exchange,list_date,type"
TYPES_DEFAULT = ["N","I","R","S","ST","TH","BB"]  # 概念/行业/地域/特色/风格/主题/宽基

def _to_date8(x):
    if pd.isna(x) or x in ("", "None", None): return None
    s = str(x)[:8]; return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"

def _fetch_type(pro, tp: str) -> pd.DataFrame:
    df = pro.ths_index(type=tp, fields=FIELDS)
    if df is None or df.empty:
        return pd.DataFrame(columns=FIELDS.split(","))
    df = df.copy()
    if "list_date" in df.columns:
        df["list_date"] = df["list_date"].apply(_to_date8)
    df.rename(columns={
        "ts_code":"ts_code_t",
        "name":"name_t",
        "count":"count_t",
        "exchange":"exchange_t",
        "list_date":"list_date_t",
        "type":"type_t",
    }, inplace=True)
    cols = ["ts_code_t","name_t","count_t","exchange_t","list_date_t","type_t"]
    for c in cols:
        if c not in df.columns: df[c] = None
    return df[cols].copy()

def _delete_all(engine):
    with engine.begin() as conn:
        res = conn.execute(text(f"DELETE FROM `{TABLE}`"))
        log.info(f"[delete] table={TABLE} rows={getattr(res,'rowcount',-1)}")

def _upsert(engine, df: pd.DataFrame) -> int:
    if df is None or df.empty: return 0
    cols = ["ts_code_t","name_t","count_t","exchange_t","list_date_t","type_t"]
    for c in cols:
        if c not in df.columns: df[c] = None
    df = df[cols].astype(object).replace({pd.NA: None, np.nan: None})
    df.drop_duplicates(subset=["ts_code_t"], keep="last", inplace=True)

    colq = ",".join(f"`{c}`" for c in cols)
    ph = ",".join(f":{c}" for c in cols)
    upd = ",".join(f"`{c}`=VALUES(`{c}`)" for c in cols if c != "ts_code_t")
    sql = f"INSERT INTO `{TABLE}` ({colq}) VALUES ({ph}) ON DUPLICATE KEY UPDATE {upd}"

    n = 0
    with engine.begin() as conn:
        for i in range(0, len(df), 3000):
            chunk = df.iloc[i:i+3000].astype(object).replace({pd.NA: None, np.nan: None})
            conn.execute(text(sql), chunk.to_dict(orient="records"))
            n += chunk.shape[0]
    return n

def main(types: str):
    pro = get_pro()
    eng = get_engine()
    tp_list = [t.strip().upper() for t in (types or "").split(",") if t.strip()] or TYPES_DEFAULT
    log.info(f"[full] table={TABLE} types={tp_list}")

    _delete_all(eng)

    total = 0
    for i, tp in enumerate(tp_list, 1):
        try:
            df = _fetch_type(pro, tp)
            n = _upsert(eng, df)
            total += n
            log.info(f"[full] {i}/{len(tp_list)} type={tp} rows={n} total={total}")
            time.sleep(0.08)
        except Exception as e:
            log.exception(f"[full] type={tp} error: {e}")
            time.sleep(0.15)

    log.info(f"[full] done table={TABLE} rows={total}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--types", default="")  # 为空=默认全部类型
    args = ap.parse_args()
    main(args.types)
