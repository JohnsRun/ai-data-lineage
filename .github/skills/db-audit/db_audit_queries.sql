-- Reference queries for the db-audit skill.
-- Use exactly one SELECT statement per request.

-- 1) Recent log table entries
SELECT ID,
       MCP_CLIENT,
       MODEL,
       END_POINT_NAME,
       CREATED_ON,
       UPDATED_ON,
       LOG_MESSAGE
  FROM DBTOOLS$MCP_LOG
 ORDER BY CREATED_ON DESC
 FETCH FIRST 5 ROWS ONLY;

-- 2) Failed call error details
SELECT ID,
       MCP_CLIENT,
       END_POINT_NAME,
       CREATED_ON,
       JSON_VALUE(LOG_MESSAGE, '$.status') AS status,
       JSON_VALUE(LOG_MESSAGE, '$.error_code') AS error_code,
       JSON_VALUE(LOG_MESSAGE, '$.error_message') AS error_message
  FROM DBTOOLS$MCP_LOG
 WHERE JSON_VALUE(LOG_MESSAGE, '$.status') = 'FAILED'
 ORDER BY CREATED_ON DESC;

-- 3) Activity by endpoint
SELECT END_POINT_NAME,
       COUNT(*) AS call_count
  FROM DBTOOLS$MCP_LOG
 GROUP BY END_POINT_NAME
 ORDER BY call_count DESC;

-- 4) Slow operations with safe numeric guard
SELECT ID,
       END_POINT_NAME,
       CREATED_ON,
       TO_NUMBER(JSON_VALUE(LOG_MESSAGE, '$.duration_ms')) AS duration_ms
  FROM DBTOOLS$MCP_LOG
 WHERE REGEXP_LIKE(JSON_VALUE(LOG_MESSAGE, '$.duration_ms'), '^\d+(\.\d+)?$')
   AND TO_NUMBER(JSON_VALUE(LOG_MESSAGE, '$.duration_ms')) > 1000
 ORDER BY duration_ms DESC;