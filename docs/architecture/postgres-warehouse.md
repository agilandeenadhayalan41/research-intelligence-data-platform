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

Evidence: the offline fake-driver suite is `OFFLINE_TESTED`. The #90 local run
makes the adapter `LOCAL_POSTGRES_VERIFIED` for the configuration recorded in
[Local validation evidence](#local-validation-evidence-90) only. That is local
PostgreSQL evidence. It is not `GCP_VERIFIED`, `CLOUD_VERIFIED`, or
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

If the session is lost (server restart, terminated backend, network drop), the
query that hits it fails with `QueryExecutionError` (`PostgreSQL connection
failed`) and is not retried. The dead connection is discarded, and the next
`query` opens a new one.

The session uses `autocommit=True` and
`default_transaction_read_only=on`. Ordinary selects do not leave an idle
transaction, and a failed autocommit statement (including a
`statement_timeout` cancel) leaves the session idle, so the next query reuses
the connection. Data-changing SQL fails in PostgreSQL instead of being detected
by a SQL parser. `TimeZone=UTC` applies to `timestamptz` display; `timestamp`
without time zone is not rewritten.

Caller SQL is not parsed, so it can still leave a live session unusable:
`BEGIN` (alone or in a multi-statement string) opens a transaction, an error
inside it aborts that transaction, and `COPY … TO STDOUT` cannot be read
through `query` and leaves a copy in progress. After any query, a connection
that is not idle outside a transaction is closed (which rolls back any open
transaction) and the next `query` opens a new session. A reconnect starts a
fresh server session: only the adapter's `autocommit`, read-only and
`TimeZone=UTC` options are reapplied, and settings the caller changed with
`SET` or `set_config` are not kept. Do not rely on session state across
`query` calls.

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

NUMERIC uses the server precision and scale when present. Otherwise scale is
the largest scale among the returned `Decimal` values and precision is the
largest integer-digit count plus that scale, so mixed-scale results such as
`avg()` output fit every row. An empty unbounded
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

## Local validation evidence (#90)

Recorded 2026-10-08 against `main` @ `e0b9271` (after PRs #89, #93, #94, #95,
#97). Evidence comment:
[#90](https://github.com/agilandeenadhayalan41/research-intelligence-data-platform/issues/90#issuecomment-6067101622).

| Item | Value |
| --- | --- |
| Server | PostgreSQL 18.6 (Ubuntu `18.6-0ubuntu0.26.04.1`) in WSL Ubuntu 26.04 |
| Start | User-owned cluster via `pg_ctl`; Unix socket only (`listen_addresses=''`, socket dir mode 0700); `local trust`, host `reject`; superuser role; `TimeZone=Etc/UTC` |
| DSN shape | `host=<user-owned socket dir> dbname=research user=<os user>` (no password) |
| Client | CPython 3.14.4; psycopg 3.2.13 `binary` (bundled libpq 17.0.6) |

| Check | Result |
| --- | --- |
| `make test-postgres-warehouse` (unmodified; collected, not skipped) | 7 passed |
| Binding (`%(name)s` bound server-side; injection payload as a value) | Pass |
| Types: int, bool, text, float, date, time, timestamp, timestamptz, bounded and mixed-scale unbounded numeric, NULL-only, empty result | Pass |
| Connection errors (missing/blank DSN, unreachable socket, unknown database/role, refused TCP, malformed DSN): fixed message, no DSN/password, no chaining | Pass |
| Lost session (terminated backend): safe error, next query on a new read-only session | Pass |
| Caller `BEGIN`, aborted transaction, `COPY … TO STDOUT`: next query on a new session; `statement_timeout` cancel keeps the session | Pass |
| Cleanup: construction opens nothing; idle after success and failure; `close()`/context manager release the backend; no probe tables or backends left | Pass |
| `make test` with the `postgres` extra installed | 973 passed, 23 deselected |
| `make check`, `make build` | Clean |

Not exercised: `make postgres-up` (docker `postgres:16`, TCP, password/SCRAM,
auth-failure messages), TLS, a non-superuser role, PostgreSQL versions other
than 18, a live server under CPython 3.12 (CI runs 3.12 and 3.14 offline only),
connect timeouts to unroutable hosts (no `connect_timeout` is set), and
concurrent use (the adapter is not thread-safe).

### Known limitations

- **Auto-prepared statements.** psycopg prepares a query server-side after it
  runs 5 times on one connection. If the table's shape then changes (for
  example `ALTER TABLE` from another connection), every later run of that query
  fails with `QueryExecutionError` until `close()`, because the session stays
  idle and is kept. Not fixed yet.
- **Read-only is a session default, not a security boundary.** Caller SQL can
  turn it off (`set_config('default_transaction_read_only', 'off', false)`,
  `BEGIN READ WRITE`). Use a read-only database role for enforcement.
- **Session overrides.** A `PGTZ` environment variable overrides the adapter's
  `TimeZone=UTC`; `timestamptz` instants stay correct, but literals without an
  offset are read in that zone. A DSN's own `options=` is replaced by the
  adapter's options.
- **Dialect quirks.** With parameters, a literal `%` must be written `%%`.
  Unused parameters are ignored. A multi-statement string without parameters
  returns one result. Unaliased duplicate expressions (`SELECT 1, 2`) are
  rejected as duplicate column names.
