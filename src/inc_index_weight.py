# -*- coding: utf-8 -*-
"""增量：指数成分及权重（index_weight）
- 用法1（推荐）：仅给 --index 下载“最新一期成分股”，并覆盖该指数该日期的数据
- 用法2：携带 --start/--end 下载区间数据（同全量但只对该指数）
"""
import argparse, time
import numpy as np, pandas as pd
from sqlalchemy import text
from utils import get_engine, get_pro, log, today_str

TABLE = "index_weight"
PKS   = ("index_code_t", "trade_date_t", "con_code_t")
FIELDS = "index_code,trade_date,con_code,con_name,weight"

def _to_date8(s: str) -> str:
    s = str(s)[:8]; return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"

def _fetch_latest(pro, code: str) -> pd.DataFrame:
    """不传日期：TuShare 返回最新一期的成分权重（单一 trade_date）"""
    df = pro.index_weight(index_code=code, fields=FIELDS)
    if df is None or df.empty:
        return pd.DataFrame(columns=FIELDS.split(","))
    return df

def _fetch_range(pro, code: str, start: str, end: str) -> pd.DataFrame:
    df = pro.index_weight(index_code=code, start_date=start, end_date=end, fields=FIELDS)
    if df is None or df.empty:
        return pd.DataFrame(columns=FIELDS.split(","))
    return df

def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["index_code_t","trade_date_t","con_code_t","con_name_t","weight_t"])
    df = df.copy()
    df["trade_date"] = df["trade_date"].astype(str).str.slice(0,8).apply(_to_date8)
    if "weight" in df.columns:
        df["weight"] = pd.to_numeric(df["weight"], errors="coerce")
    df.rename(columns={
        "index_code":"index_code_t",
        "trade_date":"trade_date_t",
        "con_code":"con_code_t",
        "con_name":"con_name_t",
        "weight":"weight_t",
    }, inplace=True)
    cols = ["index_code_t","trade_date_t","con_code_t","con_name_t","weight_t"]
    for c in cols:
        if c not in df.columns: df[c] = None
    return df[cols].copy()

def _delete_one(engine, code: str, trade_date: str = None):
    if trade_date:
        sql = (f"DELETE FROM `{TABLE}` "
               f"WHERE `index_code_t`=:code AND `trade_date_t`=:dt")
        params = {"code": code, "dt": _to_date8(trade_date)}
    else:
        sql = f"DELETE FROM `{TABLE}` WHERE `index_code_t`=:code"
        params = {"code": code}
    with engine.begin() as conn:
        res = conn.execute(text(sql), params)
        log.info(f"[delete] index={code} date={trade_date or 'ALL'} rows={getattr(res,'rowcount',-1)}")

def _upsert(engine, df: pd.DataFrame) -> int:
    if df is None or df.empty: return 0
    cols = ["index_code_t","trade_date_t","con_code_t","con_name_t","weight_t"]
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
        for i in range(0, len(df), 3000):
            chunk = df.iloc[i:i+3000].astype(object).replace({pd.NA: None, np.nan: None})
            conn.execute(text(sql), chunk.to_dict(orient="records"))
            total += chunk.shape[0]
    return total

def main(index_code: str, latest: bool, start: str, end: str):
    if not index_code:
        raise SystemExit("ERROR: --index 必填，如 000300.SH")
    pro = get_pro()
    eng = get_engine()

    if latest and not (start or end):
        raw = _fetch_latest(pro, index_code)
        df  = _normalize(raw)
        # 最新返回通常只有一个 trade_date；先删“该指数该日期”，再写入
        td = None
        if not raw.empty:
            td = str(raw["trade_date"].iloc[0])
        _delete_one(eng, index_code, td)
        n = _upsert(eng, df)
        log.info(f"[increment] index={index_code} latest_date={td} rows={n}")
        return

    # 否则按区间刷新该指数
    start = start or "19900101"
    end   = end   or today_str()
    raw = _fetch_range(pro, index_code, start, end)
    df  = _normalize(raw)
    # 精确删除该指数在区间内的数据
    if not df.empty:
        s, e = _to_date8(start), _to_date8(end)
        sql = (f"DELETE FROM `{TABLE}` "
               f"WHERE `index_code_t`=:code AND `trade_date_t`>=:s AND `trade_date_t`<=:e")
        with eng.begin() as conn:
            res = conn.execute(text(sql), {"code": index_code, "s": s, "e": e})
            log.info(f"[delete] index={index_code} range={start}~{end} rows={getattr(res,'rowcount',-1)}")
    n = _upsert(eng, df)
    log.info(f"[increment] index={index_code} range_rows={n}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", required=True, help="指数代码，如 000300.SH")
    ap.add_argument("--latest", action="store_true", help="仅刷新最新一期成分（不指定区间时默认）")
    ap.add_argument("--start",  default="", help="YYYYMMDD，可与 --end 搭配按区间刷新")
    ap.add_argument("--end",    default="", help="YYYYMMDD")
    args = ap.parse_args()
    main(args.index.strip(), args.latest, args.start.strip(), args.end.strip())
