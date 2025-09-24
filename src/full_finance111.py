# -*- coding: utf-8 -*-
"""
全量回刷模式：A股财务指标（TuShare VIP 批量）
- 自动读取库内最大 end_date_t，向前回刷 N 个月到今天
- 使用 fina_indicator_vip 按日期分段批量抓全市场
- 安全日期转换（缺失返回 None），百分比异常值清洗

用法示例：
    D:\anaconda3\envs\quant\python.exe D:\2whm\src\full_finance.py --refresh-months 12
"""
import argparse
import time
import datetime as dt
import pandas as pd
import numpy as np
from sqlalchemy import text
from dateutil.relativedelta import relativedelta

# 你项目里的公用工具
from utils import (
    get_engine, get_pro, log,
    delete_range, today_str, get_max_date
)

TABLE = "finance"
PKS = ("ts_code_t", "end_date_t")
DATE_COL = "end_date_t"
FIELDS_IND = (
    "ts_code,ann_date,f_ann_date,end_date,"
    "total_revenue,operate_profit,net_profit,total_assets,total_liab,roe,roa,gross_margin"
)

# ---------- 工具函数 ----------
def _to_date8(x):
    """把 YYYYMMDD / YYYY-MM-DD 转成 YYYY-MM-DD；非法或缺失返回 None"""
    if x is None:
        return None
    try:
        if (isinstance(x, float) and np.isnan(x)) or pd.isna(x):
            return None
    except Exception:
        pass
    s = str(x).strip()
    if s == "" or s.lower() in ("none", "nat", "nan"):
        return None
    # 仅保留数字
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) < 8:
        return None
    y, m, d = digits[:4], digits[4:6], digits[6:8]
    if y == "0000" or m == "00" or d == "00":
        return None
    return f"{y}-{m}-{d}"

def _chunks_by_days(start: str, end: str, step_days: int):
    """生成 [start, end] 的分段区间（YYYYMMDD）"""
    d0 = dt.datetime.strptime(start, "%Y%m%d").date()
    d1 = dt.datetime.strptime(end, "%Y%m%d").date()
    cur = d0
    while cur <= d1:
        nxt = min(cur + dt.timedelta(days=step_days - 1), d1)
        yield cur.strftime("%Y%m%d"), nxt.strftime("%Y%m%d")
        cur = nxt + dt.timedelta(days=1)

# ---------- 规范化 ----------
def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """字段改名、日期安全转换、数值化、异常清洗"""
    if df is None or df.empty:
        return pd.DataFrame(columns=[
            "ts_code_t","ann_date_t","f_ann_date_t","end_date_t",
            "total_revenue_t","operate_profit_t","net_profit_t",
            "total_assets_t","total_liab_t","roe_t","roa_t","gross_margin_t"
        ])

    df = df.copy()

    # 安全日期转换：直接 apply，不要先 astype(str)
    for c in ["ann_date", "f_ann_date", "end_date"]:
        if c in df.columns:
            df[c] = df[c].apply(_to_date8)

    # 改名为 *_t
    df.rename(columns={
        "ts_code": "ts_code_t",
        "ann_date": "ann_date_t",
        "f_ann_date": "f_ann_date_t",
        "end_date": "end_date_t",
        "total_revenue": "total_revenue_t",
        "operate_profit": "operate_profit_t",
        "net_profit": "net_profit_t",
        "total_assets": "total_assets_t",
        "total_liab": "total_liab_t",
        "roe": "roe_t",
        "roa": "roa_t",
        "gross_margin": "gross_margin_t",
    }, inplace=True)

    # 数值化（防止字符串进库）
    num_cols = [
        "total_revenue_t","operate_profit_t","net_profit_t",
        "total_assets_t","total_liab_t","roe_t","roa_t","gross_margin_t"
    ]
    for c in num_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    # 百分比异常兜底：绝对值 > 1000 视为异常置空，防止污染与溢出
    for c in ["roe_t", "roa_t", "gross_margin_t"]:
        if c in df.columns:
            df.loc[df[c].abs() > 1000, c] = None

    # 补列并裁剪列顺序
    cols = ["ts_code_t","ann_date_t","f_ann_date_t","end_date_t",
            "total_revenue_t","operate_profit_t","net_profit_t",
            "total_assets_t","total_liab_t","roe_t","roa_t","gross_margin_t"]
    for c in cols:
        if c not in df.columns:
            df[c] = None
    return df[cols]

# ---------- 入库 ----------
def _upsert(engine, df: pd.DataFrame) -> int:
    if df is None or df.empty:
        return 0
    cols = ["ts_code_t","ann_date_t","f_ann_date_t","end_date_t",
            "total_revenue_t","operate_profit_t","net_profit_t",
            "total_assets_t","total_liab_t","roe_t","roa_t","gross_margin_t"]
    df = df[cols].astype(object).replace({pd.NA: None, np.nan: None})
    df.drop_duplicates(subset=list(PKS), keep="last", inplace=True)

    colq = ",".join(f"`{c}`" for c in cols)
    ph   = ",".join(f":{c}" for c in cols)
    upd  = ",".join(f"`{c}`=VALUES(`{c}`)" for c in cols if c not in PKS)
    sql  = f"INSERT INTO `{TABLE}` ({colq}) VALUES ({ph}) ON DUPLICATE KEY UPDATE {upd}"

    total = 0
    with engine.begin() as conn:
        for i in range(0, len(df), 5000):
            chunk = df.iloc[i:i+5000].astype(object).replace({pd.NA: None, np.nan: None})
            conn.execute(text(sql), chunk.to_dict(orient="records"))
            total += chunk.shape[0]
    return total

# ---------- 数据抓取（VIP） ----------
def _vip_fetch(pro, s: str, e: str) -> pd.DataFrame:
    # fina_indicator_vip 支持按日期批量抓全市场
    return pro.fina_indicator_vip(start_date=s, end_date=e, fields=FIELDS_IND)

# ---------- 主流程 ----------
def main(refresh_months: int, chunk_days: int):
    pro = get_pro()
    eng = get_engine()

    # 自动计算回刷区间：从库内最大 end_date_t 往前 N 个月 ~ 今天
    end = today_str()
    max_db = get_max_date(eng, TABLE, DATE_COL)  # 返回 YYYYMMDD 或 None
    if max_db:
        d_start = (dt.datetime.strptime(max_db, "%Y%m%d").date()
                   - relativedelta(months=refresh_months))
        start = d_start.strftime("%Y%m%d")
    else:
        start = "19900101"

    log.info(f"[full-auto] table={TABLE} refresh_months={refresh_months} range={start}~{end}")

    # 先删区间再写（按 end_date_t）
    delete_range(eng, TABLE, DATE_COL, _to_date8(start), _to_date8(end))

    total = 0
    for s, e in _chunks_by_days(start, end, chunk_days):
        try:
            raw = _vip_fetch(pro, s, e)
            df  = _normalize(raw)
            n   = _upsert(eng, df)
            total += n
            log.info(f"[full-auto] {s}~{e} rows={n} total={total}")
            time.sleep(0.1)
        except Exception as ex:
            log.exception(f"[full-auto] {s}~{e} error: {ex}")
            time.sleep(0.2)

    log.info(f"[full-auto] done table={TABLE} rows={total} range={start}~{end}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh-months", type=int, default=12, help="回刷窗口（月），默认12")
    ap.add_argument("--chunk", type=int, default=31, help="按天分段抓取的窗口，默认31天")
    args = ap.parse_args()
    main(args.refresh_months, args.chunk)
