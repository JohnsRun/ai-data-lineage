---
name: db-audit
description: Use Oracle MCP to audit DBTOOLS$MCP_LOG with read-only queries for recent logs, failed calls, endpoint activity, and slow operations.
argument-hint: Ask for log table, error details, activity by endpoint, or slow operations
user-invocable: true
---

# Skill Instructions

## Purpose

Use Oracle MCP to inspect `DBTOOLS$MCP_LOG` with read-only SQL for these audit views only:

1. Recent log table entries
2. Failed call error details
3. Activity by endpoint
4. Slow operations

Use the query patterns in `db_audit_queries.sql` as the reference source.

## Hard Safety Rules (Mandatory)

1. Only run a single read-only SQL statement that begins with `SELECT` or `WITH`.
2. Never execute any non-query operation, including but not limited to:
   - DML: `INSERT`, `UPDATE`, `DELETE`, `MERGE`
   - DDL: `CREATE`, `ALTER`, `DROP`, `TRUNCATE`, `RENAME`, `COMMENT`
   - TCL: `COMMIT`, `ROLLBACK`, `SAVEPOINT`
   - DCL: `GRANT`, `REVOKE`
   - PL/SQL execution: `BEGIN`, `DECLARE`, `CALL`, `EXEC`, `EXECUTE`
   - Session/system operations: `SET`, `SHOW`, `HOST`
3. Reject multi-statement SQL.
4. Restrict queries to `DBTOOLS$MCP_LOG` only.
5. If the request is outside the supported audit scope, output only: `DB_AUDIT_MODE: LOG_TABLE_ERROR_DETAILS_ACTIVITY_SLOW_ONLY`

## Oracle MCP Usage

1. Resolve requested database/environment names case-insensitively before connecting.
2. Apply alias mapping for common environment names:
   - `DEV` -> `Oracle_DEV`
   - `UAT` -> `Oracle_UAT`
3. If the user does not specify any database/environment, default to `DEV` (resolved connection: `Oracle_DEV`).
4. Treat alias matching as case-insensitive.
5. If not connected, connect to the resolved saved connection name first.
6. If the resolved connection does not exist in saved connections, ask the user to confirm the exact saved connection name.
7. If the user provides a full connection name directly, use it as-is.
8. Execute only read-only queries through Oracle MCP query execution.
9. Return concise, evidence-based answers from query results.

## Query Selection Rules

1. Map user intent to exactly one of these audit modes:
   - `log table`
   - `error details`
   - `activity by endpoint`
   - `slow operations`
2. Use the matching SQL shape from `db_audit_queries.sql`.
3. Keep Oracle syntax compatible with SQLcl MCP.
4. For recent log output, default to the latest 5 rows unless the user asks for a different limit.
5. For slow operations, default to `duration_ms > 1000` unless the user specifies another threshold.
6. When filtering numeric JSON fields, protect `TO_NUMBER(...)` with `REGEXP_LIKE(...)` to avoid conversion errors.
7. Do not broaden to other tables or audit dimensions without explicit user approval and a skill update.

## Response Behavior

1. Run the selected safe read-only query.
2. Output query results only in markdown table format.
3. Do not add explanations, summaries, prefaces, or follow-up suggestions.
4. If no rows are returned, output only the table header.

## Supported Example Prompts

- "Show the latest MCP log rows"
- "Get failed MCP call details"
- "Show activity by endpoint"
- "List slow MCP operations over 1 second"

## Unsupported Example Prompts

- "Delete old MCP logs"
- "Create an index on DBTOOLS$MCP_LOG"
- "Show all tables in the schema"
