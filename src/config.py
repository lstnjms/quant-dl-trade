# -*- coding: utf-8 -*-
"""项目固定配置（测试期写死）"""

# TuShare Token（你提供）
TUSHARE_TOKEN = "ac24bc38642aec58624c69960f6d96abb894a3917eba7b5d7a697ea3"

# MySQL 连接（本地）
MYSQL_HOST = "localhost"
MYSQL_PORT = 3306
MYSQL_USER = "root"
MYSQL_PASSWORD = "50cdq80s1"
MYSQL_DB = "quantdb"
MYSQL_CHARSET = "utf8mb4"

# SQLAlchemy URL
DATABASE_URL = (
    f"mysql+pymysql://{MYSQL_USER}:{MYSQL_PASSWORD}@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DB}"
    f"?charset={MYSQL_CHARSET}"
)

# 默认 schema（MySQL 可忽略）
DB_SCHEMA = None

# 写入策略
WRITE_CHUNKSIZE = 1000
POOL_SIZE = 5
MAX_OVERFLOW = 10
ECHO_SQL = False
