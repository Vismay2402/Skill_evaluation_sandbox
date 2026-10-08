# Acme Snowflake rules

| ID | Severity | Rule |
|---|---|---|
| SF-01 | BLOCKER | `DELETE` or `UPDATE` without a `WHERE` clause. |
| SF-02 | BLOCKER | Any statement against `PROD_*` databases that drops or truncates (`DROP`, `TRUNCATE`). |
| SF-03 | MAJOR | `SELECT *` in views, dbt models or anything that is persisted. |
| SF-04 | MAJOR | Object names must be fully qualified `DATABASE.SCHEMA.OBJECT`. |
| SF-05 | MAJOR | Joins on `customer_id` must also join on `region` (customer ids are only unique per region). |
| SF-06 | MINOR | Use `QUALIFY ROW_NUMBER() ...` for de-duplication instead of a subquery with `WHERE rn = 1`. |
| SF-07 | MINOR | Timestamps must be `TIMESTAMP_NTZ` in UTC; flag `CURRENT_TIMESTAMP()` (session time zone) - use `SYSDATE()`. |
| SF-08 | MINOR | Warehouse must be set explicitly (`USE WAREHOUSE`) in scripts, never rely on the user default. |
