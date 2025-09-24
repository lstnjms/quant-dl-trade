# -*- coding: utf-8 -*-
"""增量：同花顺概念板块成分（ths_member）
- 注意：接口不带日期，故“增量=刷新当前成分”
- 策略：逐板块 delete + insert，幂等更新（同全量）
"""
import time, numpy as np, pandas as pd
from sqlalchemy import text
from utils import get_engine, get_pro, log

TABLE = "ths_member"
PKS   = ("ts_code_t","con_code_t")
FIELDS = "ts_code,con_code,con_name,weight,in_date,out_date,is_new"

def _to_date8(s: str):
    if pd.isna(s) or s in (None, "", "None"): return None
    s = str(s)[:8]
    return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"

def _get_concept_codes(pro):
    df = pro.ths_index(type='N', fields="ts_code")
    if df is None or df.empty: return []
    return df["ts_code"].dropna().astype(str).tolist()

def _fetch_members(pro, code: str) -> pd.DataFrame:
    df = pro.ths_member(ts_code=code, fields=FIELDS)
    if df is None or df.empty:
        return pd.DataFrame(columns=FIELDS.split(","))
    df = df.copy()
    df["in_date"]  = df.get("in_date").apply(_to_date8) if "in_date" in df.columns else None
    df["out_date"] = df.get("out_date").apply(_to_date8) if "out_date" in df.columns else None
    df.rename(columns={
        "ts_code":"ts_code_t",
        "con_code":"con_code_t",
        "con_name":"con_name_t",
        "weight":"weight_t",
        "in_date":"in_date_t",
        "out_date":"out_date_t",
        "is_new":"is_new_t",
    }, inplace=True)
    cols = ["ts_code_t","con_code_t","con_name_t","weight_t","in_date_t","out_date_t","is_new_t"]
    for c in cols:
        if c not in df.columns: df[c] = None
    return df[cols].copy()

def _delete_one_board(engine, ts_code: str):
    sql = f"DELETE FROM `{TABLE}` WHERE `ts_code_t`=:ts"
    with engine.begin() as conn:
        res = conn.execute(text(sql), {"ts": ts_code})
        log.info(f"[delete] board={ts_code} rows={getattr(res, 'rowcount', -1)}")

def _upsert(engine, df: pd.DataFrame) -> int:
    if df is None or df.empty: return 0
    cols = ["ts_code_t","con_code_t","con_name_t","weight_t","in_date_t","out_date_t","is_new_t"]
    df = df.copy()
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
        for i in range(0, len(df), 2000):
            chunk = df.iloc[i:i+2000].astype(object).replace({pd.NA: None, np.nan: None})
            conn.execute(text(sql), chunk.to_dict(orient="records"))
            total += chunk.shape[0]
    return total

def main():
    pro = get_pro()
    eng = get_engine()
    boards = _get_concept_codes(pro)
    log.info(f"[increment] table={TABLE} concept_boards={len(boards)}")
    total = 0
    for i, b in enumerate(boards, 1):
        try:
            _delete_one_board(eng, b)
            df = _fetch_members(pro, b)
            n  = _upsert(eng, df)
            total += n
            if n or (i % 50 == 0):
                log.info(f"[increment] {i}/{len(boards)} board={b} rows={n} total={total}")
            time.sleep(0.06)
        except Exception as e:
            log.exception(f"[increment] {i}/{len(boards)} board={b} error: {e}")
            time.sleep(0.12)
    log.info(f"[increment] done rows={total}")

if __name__ == "__main__":
    main()
