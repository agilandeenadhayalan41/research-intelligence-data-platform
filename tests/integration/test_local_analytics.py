import duckdb
import pyarrow as pa
import pytest


@pytest.mark.integration
def test_duckdb_arrow_interchange_with_synthetic_data() -> None:
    """Verify local tool compatibility, not a working warehouse adapter."""
    table = pa.table({"work_id": ["synthetic-1", "synthetic-2"], "citations": [2, 3]})
    with duckdb.connect(":memory:") as connection:
        connection.register("synthetic_works", table)
        result = connection.execute(
            "SELECT sum(citations) AS total FROM synthetic_works WHERE citations >= ?",
            [2],
        ).to_arrow_table()
    assert isinstance(result, pa.Table)
    assert result.to_pylist() == [{"total": 5}]
