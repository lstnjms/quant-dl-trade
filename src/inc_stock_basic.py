# -*- coding: utf-8 -*-
"""增量下载：stock_basic（按 list_date 增量 + UPSERT 更新变更字段）"""
import pandas as pd
from datetime import datetime, timedelta
from utils import get_engine, get_pro, log, get_max_date, today_str, map_stock_basic_columns, upsert_mysql

TABLE    = "stock_basic"
DATE_COL = "list_date_t"
FIELDS = "ts_code,symbol,name,area,industry,market,exchange,list_status,list_date,delist_date,is_hs,fullname,enname,cnspell"

def fetch_by_range(start: str, end: str) -> pd.DataFrame:
    """stock_basic 本身不支持按日期过滤，这里策略是：
    拉取全量（L/D/P），再按 list_date 在 [start,end] 过滤，视作“新增上市的增量”。
    对于名称/行业变更等，靠 UPSERT 覆盖旧纪录。
    """
    pro = get_pro()
    dfs = []
    for status in ["L", "D", "P"]:
        df = pro.stock_basic(list_status=status, fields=FIELDS)
        if df is not None and not df.empty:
            dfs.append(df)
    if not dfs:
        return pd.DataFrame()
    df = pd.concat(dfs, ignore_index=True).drop_duplicates(subset=["ts_code"], keep="last")
    if "list_date" in df.columns:
        m = (df["list_date"] >= start) & (df["list_date"] <= end)
        df = df.loc[m].copy()
    return df

def main():
    eng = get_engine()
    last = get_max_date(eng, TABLE, DATE_COL)
    if last:
        s = (datetime.strptime(last, "%Y%m%d").date() + timedelta(days=1)).strftime("%Y%m%d")
    else:
        s = today_str()
    e = today_str()
    log.info(f"[increment] table={TABLE} range={s}~{e} last={last}")

    df = fetch_by_range(s, e)
    if df is None: df = pd.DataFrame()
    df2 = map_stock_basic_columns(df)
    rows = upsert_mysql(eng, TABLE, df2, pk="ts_code_t")
    log.info(f"[increment] done table={TABLE} rows={rows} range={s}~{e}")
    return rows

if __name__ == "__main__":
    main()
