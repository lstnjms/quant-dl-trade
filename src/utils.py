# -*- coding: utf-8 -*-
import logging, os, time
from typing import Optional
from datetime import date
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

import tushare as ts

# ---------------- Config ----------------
from config import (
    TUSHARE_TOKEN, DATABASE_URL, DB_SCHEMA, WRITE_CHUNKSIZE,
    POOL_SIZE, MAX_OVERFLOW, ECHO_SQL
)

# ---------------- Logger ----------------
def get_logger(name: str = "sync") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    # file
    fh = logging.FileHandler("sync_debug.log", encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    # console
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    return logger

log = get_logger()

# ---------------- Engine & TuShare ----------------
_engine_cache: Optional[Engine] = None
_pro_cache = None

def get_engine() -> Engine:
    global _engine_cache
    if _engine_cache is None:
        _engine_cache = create_engine(
            DATABASE_URL,
            pool_size=POOL_SIZE,
            max_overflow=MAX_OVERFLOW,
            echo=ECHO_SQL,
            future=True
        )
    return _engine_cache

def get_pro():
    global _pro_cache
    if _pro_cache is None:
        ts.set_token(TUSHARE_TOKEN)
        _pro_cache = ts.pro_api()
    return _pro_cache

# ---------------- Helpers ----------------
def today_str() -> str:
    return date.today().strftime("%Y%m%d")

def to_yyyymmdd(x) -> Optional[str]:
    if x is None:
        return None
    s = str(x).replace("-", "").replace("/", "")
    return s[:8]

def retry(fn, tries=3, backoff=1.5, what="call"):
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            last = e
            wait = backoff ** i
            log.warning(f"[retry] {what} failed {i+1}/{tries}, sleep={wait:.1f}s err={e}")
            time.sleep(wait)
    raise last

# ---------------- DB ops ----------------
def get_max_date(engine: Engine, table: str, date_col: str) -> Optional[str]:
    sql = f"SELECT MAX({date_col}) FROM {table}"
    try:
        with engine.connect() as c:
            v = c.execute(text(sql)).scalar()
        if not v:
            return None
        if hasattr(v, "strftime"):
            return v.strftime("%Y%m%d")
        return to_yyyymmdd(v)
    except SQLAlchemyError as e:
        log.error(f"[db] get_max_date failed table={table} col={date_col} err={e}")
        return None

def delete_range(engine: Engine, table: str, date_col: str, start: str, end: str, schema: Optional[str] = DB_SCHEMA) -> int:
    tbl = f"`{table}`" if not schema else f"`{schema}`.`{table}`"
    sql = f"DELETE FROM {tbl} WHERE {date_col} BETWEEN :s AND :e"
    try:
        with engine.begin() as c:
            n = c.execute(text(sql), {"s": start, "e": end}).rowcount or 0
        log.info(f"[delete] table={table} {date_col} in [{start}~{end}] rows={n}")
        return int(n)
    except SQLAlchemyError as e:
        log.error(f"[db] delete_range failed table={table} err={e}")
        return 0

# ---------------- stock_basic column mapping ----------------
def map_stock_basic_columns(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=[
            "ts_code_t","symbol_t","name_t","area_t","industry_t","market_t",
            "list_date_t","delist_date_t","is_hsgt_t","is_shsc_t","enname_t",
            "company_name_t","market_cap_t"
        ])
    out = pd.DataFrame()
    def has(c): return c in df.columns
    if has("ts_code"): out["ts_code_t"] = df["ts_code"]
    if has("symbol"):  out["symbol_t"]  = df["symbol"]
    if has("name"):    out["name_t"]    = df["name"]
    if has("area"):    out["area_t"]    = df["area"]
    if has("industry"):out["industry_t"]= df["industry"]
    if has("market"):  out["market_t"]  = df["market"]
    if has("enname"):  out["enname_t"]  = df["enname"]
    if has("fullname"):out["company_name_t"] = df["fullname"]
    if has("list_date"):
        out["list_date_t"] = pd.to_datetime(df["list_date"], errors="coerce").dt.date
    else:
        out["list_date_t"] = pd.NaT
    if has("delist_date"):
        out["delist_date_t"] = pd.to_datetime(df["delist_date"], errors="coerce").dt.date
    else:
        out["delist_date_t"] = pd.NaT
    if has("is_hs"):
        hs = (df["is_hs"] + "").fillna("N").str.upper()
        out["is_hsgt_t"] = hs
        mapping = {"H": 1, "S": 2}
        out["is_shsc_t"] = hs.map(mapping).fillna(0).astype("int64")
    else:
        out["is_hsgt_t"] = None
        out["is_shsc_t"] = 0
    out["market_cap_t"] = None
    if "ts_code_t" in out.columns:
        out = out.drop_duplicates(subset=["ts_code_t"], keep="last")
    return out

# ---------------- Insert / Upsert ----------------
def to_sql_append(engine: Engine, table: str, df: pd.DataFrame, schema: Optional[str]=DB_SCHEMA) -> int:
    if df is None or df.empty:
        log.info(f"[insert] table={table} rows=0 (empty)")
        return 0
    rows = int(df.shape[0])
    log.info(f"[insert] start table={table} rows={rows}")
    df.to_sql(table, con=engine, schema=schema, if_exists="append", index=False, chunksize=WRITE_CHUNKSIZE, method=None)
    log.info(f"[insert] done  table={table} rows={rows}")
    return rows

def upsert_mysql(engine: Engine, table: str, df: pd.DataFrame, schema: Optional[str]=DB_SCHEMA, pk: str="ts_code_t") -> int:
    """MySQL ON DUPLICATE KEY UPDATE，要求表有主键（如 ts_code_t）。"""
    if df is None or df.empty:
        log.info(f"[upsert] table={table} rows=0 (empty)")
        return 0
    total = 0
    cols = list(df.columns)
    col_quoted = ",".join([f"`{c}`" for c in cols])
    val_placeholder = ",".join([f":{c}" for c in cols])
    update_clause = ",".join([f"`{c}`=VALUES(`{c}`)" for c in cols if c != pk])
    sql = (
        f"INSERT INTO `{table}` ({col_quoted}) VALUES ({val_placeholder}) "
        f"ON DUPLICATE KEY UPDATE {update_clause}"
    )
    with engine.begin() as conn:
        for i in range(0, len(df), WRITE_CHUNKSIZE):
            chunk = df.iloc[i:i+WRITE_CHUNKSIZE].copy()
            values = chunk.to_dict(orient="records")
            n = conn.execute(text(sql), values).rowcount or 0
            total += n
    log.info(f"[upsert] done  table={table} rows={int(df.shape[0])} (affected={total})")
    return int(df.shape[0])
