# PostgreSQL Warehouse query adapter

`PostgreSQLWarehouse` implements the existing read `Warehouse.query` contract
for PostgreSQL. BigQuery remains the first GCP analytical implementation.
`DuckDBWarehouse` (#8) is the separate local query adapter; this adapter does
not change it.

This adapter is a supported PostgreSQL **query backend** only. It is not:

- `PostgresControlStore` or `PostgresCanonicalStore`
- the ingestion unit of work
- a Data Service backend
- a PostgreSQL/AlloyDB serving projection

Issue **#23** stays conditional. Completing this adapter does not approve #23,
does not replace BigQuery, and does not mean PostgreSQL is production-ready.

Evidence for the offline fake-driver suite is `OFFLINE_TESTED`. A successful
`make test-postgres-warehouse` run against an explicit local `POSTGRES_DSN` is
local PostgreSQL evidence only. It is not `GCP_VERIFIED`, `CLOUD_VERIFIED`, or
`ALLOYDB_VERIFIED`.

## Lifecycle

Construction stores `WarehouseConfig` and does not read the DSN, import
`psycopg`, open a socket, or run SQL.

`config.postgres_dsn_env` is the **name** of the environment variable (default
`POSTGRES_DSN`), not the DSN. The value is read only when the first `query`
opens a connection. A missing or blank value fails with a fixed message and is
not logged. There is no localhost fallback and no DSN in YAML.

The adapter owns that connection and reuses it for later queries. `close()`
and the context manager close it. A closed adapter does not reconnect; further
`query` calls raise `WarehouseError`.

The session uses `autocommit=True` and
`default_transaction_read_only=on`. Ordinary selects do not leave an idle
transaction. A failed statement does not leave an aborted transaction, so a
later query can reuse the connection. Data-changing SQL fails in PostgreSQL
instead of being detected by a SQL parser. `TimeZone=UTC` applies to
`timestamptz` display; `timestamp` without time zone is not rewritten.

This differs from ingestion `connect_postgres`, which keeps `autocommit=False`
for explicit write transactions. Warehouse queries do not use that helper.

The adapter is synchronous and is not thread-safe. It does not pool
connections.

`psycopg` stays an optional `postgres` extra. Importing the package without
that extra works. `query` without an injected connection fails with
`WarehouseError` (`PostgreSQL support is not available`) when the driver is
missing.

## Parameters and results

PostgreSQL placeholders are psycopg pyformat `%(name)s`. Names must be simple
identifiers. Values are driver-bound and are never formatted into SQL. `:name`
and BigQuery `@name` are different dialect contracts and are not rewritten.

`query` returns a `pyarrow.Table`, including zero-row results. Column order
follows the result metadata. Duplicate column names raise
`ArrowConversionError` rather than dropping a column.

NULL stays Arrow null. Supported result types:

| PostgreSQL | Arrow |
| --- | --- |
| int2 / int4 / int8 | `int64` |
| bool | `bool` |
| text / varchar / char | `string` |
| float4 / float8 | `float64` |
| date | `date32` |
| time (no time zone) | `time64[us]` |
| timestamp | `timestamp[us]` (naive) |
| timestamptz | `timestamp[us, tz=UTC]` (aware instant) |
| numeric / decimal | `decimal128` or `decimal256` |

NUMERIC uses the server precision and scale when present. Otherwise precision
and scale are taken from the returned `Decimal` values. An empty unbounded
NUMERIC result uses `decimal128(38, 18)` because no values are present.
Values that cannot be represented without losing precision raise
`ArrowConversionError`. Other PostgreSQL types, including extension types and
`timetz`, are rejected the same way. Nothing is coerced with `str(...)`.

`timestamp` and `timestamptz` are not interchangeable. Naive and aware values
are not stripped or given an invented offset by this adapter.

Statements that do not produce a result set fail as `QueryExecutionError`.
There is no `execute`, migration, or copy API on this class.

## Errors and logs

| Condition | Error |
| --- | --- |
| Missing DSN, closed adapter, missing driver | `WarehouseError` |
| Invalid SQL argument or parameter mapping/value | `QueryParameterError` |
| Connection or query failure, non-result statement | `QueryExecutionError` |
| Unsafe or unknown Arrow conversion | `ArrowConversionError` |

Messages are fixed categories. DSNs, parameter values, and result rows are not
logged. Success logs may include `operation=query`, `backend=postgres`,
`row_count`, and `column_count`.

## Tests

Default `make test` runs `tests/unit/test_postgres_warehouse.py` with a fake
connection. That does not prove PostgreSQL dialect or server behavior.

Local runtime coverage:

```text
make test-postgres-warehouse
```

That target installs the `postgres` extra and runs
`tests/integration/test_postgres_warehouse_integration.py`. It skips when
`POSTGRES_DSN` is unset. Docker is not started by the test or by the adapter.
