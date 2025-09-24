# -*- coding: utf-8 -*-
"""全量下载：stock_basic（先删区间数据，再重下）"""
import argparse
import pandas as pd
from datetime import date
from utils import get_engine, get_pro, log, delete_range, map_stock_basic_columns, to_sql_append

TABLE    = "stock_basic"
DATE_COL = "list_date_t"
FIELDS = "ts_code,symbol,name,area,industry,market,exchange,list_status,list_date,delist_date,is_hs,fullname,enname,cnspell"

def today_str() -> str:
    return date.today().strftime("%Y%m%d")

def fetch_full() -> pd.DataFrame:
    pro = get_pro()
    # 拉 L/D/P 三种状态，避免漏退市/待上市
    dfs = []
    for status in ["L", "D", "P"]:
        df = pro.stock_basic(list_status=status, fields=FIELDS)
        if df is not None and not df.empty:
            dfs.append(df)
    if not dfs:
        return pd.DataFrame()
    df = pd.concat(dfs, ignore_index=True).drop_duplicates(subset=["ts_code"], keep="last")
    return df

def main(start: str, end: str):
    eng = get_engine()
    log.info(f"[full] table={TABLE} range={start}~{end}")

    # 1) 删除区间数据（全量跑就清乾净；19700101~今天 等价于“全库重下”）
    delete_range(eng, TABLE, DATE_COL, start, end)

    # 2) 下载
    df = fetch_full()
    if df is None:
        df = pd.DataFrame()

    # 3) 更稳的日期过滤：
    #    - 将 list_date 转 datetime（errors='coerce'）
    #    - 解析失败的（NaT）一律保留，避免误删
    if not df.empty:
        s = pd.to_datetime(start, format="%Y%m%d", errors="coerce")
        e = pd.to_datetime(end,   format="%Y%m%d", errors="coerce")
        if "list_date" in df.columns:
            ld = pd.to_datetime(df["list_date"], errors="coerce")
            mask = ld.isna() | ((ld >= s) & (ld <= e))   # NaT 保留
            before = len(df)
            df = df.loc[mask].copy()
            log.info(f"[full] filter by list_date kept={len(df)}/{before} (NaT kept={ld.isna().sum()})")

    # 4) 映射并写库
    df2 = map_stock_basic_columns(df)
    rows = to_sql_append(eng, TABLE, df2)
    # 记录下载数据的自然范围
    min_d = df["list_date"].min() if "list_date" in df.columns and not df.empty else None
    max_d = df["list_date"].max() if "list_date" in df.columns and not df.empty else None
    log.info(f"[full] done table={TABLE} rows={rows} range={start}~{end} df_range={min_d}~{max_d}")
    return rows

if __name__ == "__main__":
    # 改成“默认即可跑”：开始=19700101，结束=今天；也支持手动覆盖
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="19700101", help="YYYYMMDD; default=19700101")
    ap.add_argument("--end",   default=today_str(), help="YYYYMMDD; default=today")
    args = ap.parse_args()
    main(args.start.strip(), args.end.strip())
