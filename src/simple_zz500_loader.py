SET @INDEX_CODE := '000905.SH';

WITH maxd AS (
  SELECT MAX(trade_date_t) AS d FROM stock_daily
),
zz500_codes AS (
  SELECT sd.ts_code_t
  FROM stock_daily sd
  JOIN maxd m ON sd.trade_date_t = m.d
),
codes_with_name AS (
  SELECT z.ts_code_t AS con_code_t, sb.name_t AS con_name_t
  FROM zz500_codes z
  LEFT JOIN stock_basic sb ON sb.ts_code_t = z.ts_code_t
)
INSERT INTO index_weight(index_code_t, trade_date_t, con_code_t, con_name_t, weight_t)
SELECT @INDEX_CODE, NULL, con_code_t, con_name_t, NULL
FROM codes_with_name
ON DUPLICATE KEY UPDATE
  con_name_t = VALUES(con_name_t),
  weight_t   = VALUES(weight_t);
