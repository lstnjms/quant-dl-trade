# -*- coding: utf-8 -*-
"""全量：同花顺板块指数日行情（ths_daily），字段按 doc 截图 14 项"""
import argparse, time
import numpy as np, pandas as pd
from sqlalchemy import text
from utils import get_engine, get_pro, log, delete_range, today_str

TABLE = "ths_daily"
PKS = ("ts_code_t","trade_date_t")

FIELDS = ("ts_code,trade_date,close,open,high,low,pre_close,avg_price,"
          "change,pct_change,vol,turnover_rate,total_mv,float_mv")

def _to_date(s:str)->str:
    s=str(s)[:8]; return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"

def _get_ths_codes(pro):
    df = pro.ths_index(fields="ts_code")
    return [] if df is None or df.empty else df.ts_code.dropna().astype(str).tolist()

def _fetch_one(pro, code, start, end):
    df = pro.ths_daily(ts_code=code, start_date=start, end_date=end, fields=FIELDS)
    if df is None or df.empty:
        return pd.DataFrame(columns=FIELDS.split(","))
    df = df.copy()
    df["trade_date"] = df["trade_date"].astype(str).str.slice(0,8).apply(_to_date)

    for c in ["close","open","high","low","pre_close","avg_price",
              "change","pct_change","vol","turnover_rate","total_mv","float_mv"]:
        if c in df.columns: df[c] = pd.to_numeric(df[c], errors="coerce")

    df.rename(columns={
        "ts_code":"ts_code_t","trade_date":"trade_date_t",
        "close":"close_t","open":"open_t","high":"high_t","low":"low_t",
        "pre_close":"pre_close_t","avg_price":"avg_price_t",
        "change":"change_t","pct_change":"pct_change_t","vol":"vol_t",
        "turnover_rate":"turnover_rate_t","total_mv":"total_mv_t","float_mv":"float_mv_t",
    }, inplace=True)

    cols = ["ts_code_t","trade_date_t","close_t","open_t","high_t","low_t",
            "pre_close_t","avg_price_t","change_t","pct_change_t","vol_t",
            "turnover_rate_t","total_mv_t","float_mv_t"]
    for c in cols:
        if c not in df.columns: df[c]=None
    return df[cols].copy()

def _upsert(engine, df):
    if df is None or df.empty: return 0
    cols = ["ts_code_t","trade_date_t","close_t","open_t","high_t","low_t",
            "pre_close_t","avg_price_t","change_t","pct_change_t","vol_t",
            "turnover_rate_t","total_mv_t","float_mv_t"]
    df = df.copy()
    for c in cols:
        if c not in df.columns: df[c]=None
    df = df[cols].astype(object).replace({pd.NA:None, np.nan:None})
    df.drop_duplicates(subset=list(PKS), keep="last", inplace=True)

    colq = ",".join(f"`{c}`" for c in cols)
    ph   = ",".join(f":{c}" for c in cols)
    upd  = ",".join(f"`{c}`=VALUES(`{c}`)" for c in cols if c not in PKS)
    sql  = f"INSERT INTO `{TABLE}` ({colq}) VALUES ({ph}) ON DUPLICATE KEY UPDATE {upd}"

    n=0
    with engine.begin() as conn:
        for i in range(0,len(df),1500):
            chunk=df.iloc[i:i+1500].astype(object).replace({pd.NA:None,np.nan:None})
            conn.execute(text(sql), chunk.to_dict(orient="records"))
            n+=chunk.shape[0]
    return n

def main(start, end):
    pro=get_pro(); eng=get_engine()
    codes=_get_ths_codes(pro)
    log.info(f"[full] table={TABLE} codes={len(codes)} range={start}~{end}")
    delete_range(eng, TABLE, "trade_date_t", _to_date(start), _to_date(end))

    total=0
    for i,code in enumerate(codes,1):
        try:
            df=_fetch_one(pro, code, start, end)
            total += _upsert(eng, df)
            if i%100==0: log.info(f"[full] {i}/{len(codes)} {code} total={total}")
            time.sleep(0.08)
        except Exception as e:
            log.exception(f"[full] {i}/{len(codes)} {code} error: {e}")
            time.sleep(0.15)
    log.info(f"[full] done rows={total}")

if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--start", default="19900101")
    ap.add_argument("--end",   default=today_str())
    a=ap.parse_args()
    main(a.start.strip(), a.end.strip())
