"""DuckDB-backed CSV analytics + CRUD over the vault."""

from __future__ import annotations

from pathlib import Path

import pytest

from nexus import vault, vault_csv


@pytest.fixture
def isolated_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(vault, "_VAULT_ROOT", tmp_path)
    return tmp_path


def _seed(root: Path, name: str, text: str) -> str:
    (root / name).write_text(text, encoding="utf-8")
    return name


SAMPLE = (
    "id,name,city,age\n"
    "1,Alice,Sao Paulo,30\n"
    "2,Bob,Rio,25\n"
    "3,Carol,Sao Paulo,40\n"
    "4,Dan,Rio,35\n"
    "5,Eve,Belo Horizonte,28\n"
)


def test_schema(isolated_vault: Path) -> None:
    p = _seed(isolated_vault, "people.csv", SAMPLE)
    out = vault_csv.csv_schema(p)
    cols = {c["name"] for c in out["columns"]}
    assert cols == {"id", "name", "city", "age"}
    assert out["row_count"] == 5
    assert out["file_size"] > 0


def test_sample_modes(isolated_vault: Path) -> None:
    p = _seed(isolated_vault, "people.csv", SAMPLE)
    head = vault_csv.csv_sample(p, mode="head", n=2)
    assert len(head["rows"]) == 2
    assert head["rows"][0]["name"] == "Alice"

    tail = vault_csv.csv_sample(p, mode="tail", n=2)
    assert len(tail["rows"]) == 2
    names = {r["name"] for r in tail["rows"]}
    assert names == {"Eve", "Dan"}

    rand = vault_csv.csv_sample(p, mode="random", n=3)
    assert len(rand["rows"]) == 3


def test_describe(isolated_vault: Path) -> None:
    p = _seed(isolated_vault, "people.csv", SAMPLE)
    out = vault_csv.csv_describe(p)
    by_col = {s["column"]: s for s in out["stats"]}
    assert by_col["age"]["count"] == 5
    assert by_col["age"]["min"] == 25
    assert by_col["age"]["max"] == 40
    assert "mean" in by_col["age"]
    # categorical
    assert "top_values" in by_col["city"]
    top_cities = {tv["value"] for tv in by_col["city"]["top_values"]}
    assert "Sao Paulo" in top_cities


def test_query_select_only_columnar_default(isolated_vault: Path) -> None:
    p = _seed(isolated_vault, "people.csv", SAMPLE)
    out = vault_csv.csv_query(p, "SELECT city, count(*) AS n FROM t GROUP BY city ORDER BY n DESC")
    assert out["format"] == "columns"
    assert out["columns"] == ["city", "n"]
    by_city = {row[0]: row[1] for row in out["data"]}
    assert by_city["Sao Paulo"] == 2
    assert by_city["Rio"] == 2
    assert by_city["Belo Horizonte"] == 1
    assert "rows" not in out


def test_query_rows_format_backcompat(isolated_vault: Path) -> None:
    p = _seed(isolated_vault, "people.csv", SAMPLE)
    out = vault_csv.csv_query(
        p,
        "SELECT city, count(*) AS n FROM t GROUP BY city ORDER BY n DESC",
        fmt="rows",
    )
    assert out["format"] == "rows"
    by_city = {r["city"]: r["n"] for r in out["rows"]}
    assert by_city["Sao Paulo"] == 2
    assert "data" not in out


def test_query_rejects_invalid_fmt(isolated_vault: Path) -> None:
    p = _seed(isolated_vault, "people.csv", SAMPLE)
    with pytest.raises(ValueError):
        vault_csv.csv_query(p, "SELECT 1", fmt="parquet")


def test_query_rejects_non_select(isolated_vault: Path) -> None:
    p = _seed(isolated_vault, "people.csv", SAMPLE)
    with pytest.raises(ValueError):
        vault_csv.csv_query(p, "DELETE FROM t")
    with pytest.raises(ValueError):
        vault_csv.csv_query(p, "UPDATE t SET age = 0")


def test_query_truncates(isolated_vault: Path) -> None:
    p = _seed(isolated_vault, "people.csv", SAMPLE)
    out = vault_csv.csv_query(p, "SELECT * FROM t", limit=2)
    assert out["truncated"] is True
    assert len(out["data"]) == 2
    assert out["row_count"] == 5


def test_query_caps_limit_at_max(isolated_vault: Path) -> None:
    p = _seed(isolated_vault, "people.csv", SAMPLE)
    out = vault_csv.csv_query(p, "SELECT * FROM t", limit=999_999)
    # Tightened cap — sanity-check it's the documented MAX, not the requested value.
    assert out["limit"] == vault_csv._MAX_QUERY_LIMIT
    assert out["limit"] == 200


def test_relationships(isolated_vault: Path) -> None:
    _seed(isolated_vault, "people.csv", SAMPLE)
    _seed(
        isolated_vault,
        "orders.csv",
        "order_id,user_id,total\n"
        "100,1,49.90\n"
        "101,2,12.50\n"
        "102,3,99.00\n"
        "103,1,7.00\n",
    )
    out = vault_csv.csv_relationships("orders.csv")
    pairs = [(m["left"]["column"], m["right"]["column"]) for m in out["matches"]]
    # user_id (orders) should match id (people)
    assert ("user_id", "id") in pairs
    top = out["matches"][0]
    assert top["value_overlap"] >= 0.5


def test_crud_roundtrip(isolated_vault: Path) -> None:
    p = _seed(isolated_vault, "people.csv", SAMPLE)

    # append
    res = vault_csv.csv_append_row(p, {"id": 6, "name": "Frank", "city": "Rio", "age": 22})
    assert res["total_rows"] == 6
    assert vault_csv.csv_schema(p)["row_count"] == 6

    # update
    vault_csv.csv_update_cell(p, row_index=0, column="age", value=31)
    page = vault_csv.csv_read_page(p, limit=10)
    assert page["rows"][0]["age"] == "31"

    # delete
    vault_csv.csv_delete_row(p, row_index=0)
    assert vault_csv.csv_schema(p)["row_count"] == 5

    # rename / drop column
    vault_csv.csv_set_schema(p, [
        {"name": "user_id", "rename_from": "id"},
        {"name": "name"},
        {"name": "age"},
    ])
    schema = vault_csv.csv_schema(p)
    assert {c["name"] for c in schema["columns"]} == {"user_id", "name", "age"}


def test_unknown_column_in_update(isolated_vault: Path) -> None:
    p = _seed(isolated_vault, "people.csv", SAMPLE)
    with pytest.raises(ValueError):
        vault_csv.csv_update_cell(p, 0, "ghost", "x")


def test_non_csv_path_rejected(isolated_vault: Path) -> None:
    _seed(isolated_vault, "note.md", "# hi\n")
    with pytest.raises(ValueError):
        vault_csv.csv_schema("note.md")


def test_analyze_basic(isolated_vault: Path) -> None:
    p = _seed(isolated_vault, "people.csv", SAMPLE)
    script = "print(len(t))"
    out = vault_csv.csv_analyze(p, script)
    assert out["ok"] is True
    assert "5" in out["output"]
    assert out["path"] == p
    assert out.get("truncated") is False


def test_analyze_aggregation(isolated_vault: Path) -> None:
    pytest.importorskip("pandas")
    p = _seed(isolated_vault, "people.csv", SAMPLE)
    script = (
        "result = t.groupby('city')['age'].mean().reset_index()\n"
        "print(result.to_markdown(index=False))"
    )
    out = vault_csv.csv_analyze(p, script)
    assert out["ok"] is True
    assert "Sao Paulo" in out["output"]


def test_analyze_error(isolated_vault: Path) -> None:
    p = _seed(isolated_vault, "people.csv", SAMPLE)
    out = vault_csv.csv_analyze(p, "1 / 0")
    assert out["ok"] is False
    assert out.get("error") or out.get("exit_code")


def test_analyze_truncated_large_csv(isolated_vault: Path) -> None:
    rows = "id,val\n" + "\n".join(f"{i},{i * 10}" for i in range(11_000))
    p = _seed(isolated_vault, "big.csv", rows)
    out = vault_csv.csv_analyze(p, "print(len(t))")
    assert out["ok"] is True
    assert out["truncated"] is True
    assert out["row_count"] == 11_000
    assert out["row_count_loaded"] == 10_000
    assert "10000" in out["output"]


def test_analyze_full_coverage_validation(isolated_vault: Path) -> None:
    rows = "id,val\n" + "\n".join(f"{i},{i * 10}" for i in range(11_000))
    p = _seed(isolated_vault, "big.csv", rows)
    out = vault_csv.csv_analyze(p, "assert_full_coverage()")
    assert "WARN assert_full_coverage" in out["output"]


def test_query_auto_summarize(isolated_vault: Path) -> None:
    rows = "id,name\n" + "\n".join(f"{i},item_{i}" for i in range(50))
    p = _seed(isolated_vault, "items.csv", rows)
    out = vault_csv.csv_query(p, "SELECT * FROM t")
    assert out["summarized"] is True
    assert "summary" in out
    assert len(out["summary"]) == 2
    assert "data" not in out
    assert "data_head" in out
    assert len(out["data_head"]) == 3


def test_query_summarize_forced_off(isolated_vault: Path) -> None:
    rows = "id,name\n" + "\n".join(f"{i},item_{i}" for i in range(50))
    p = _seed(isolated_vault, "items.csv", rows)
    out = vault_csv.csv_query(p, "SELECT * FROM t", summarize=False)
    assert out.get("summarized") is not True
    assert "data" in out
    assert len(out["data"]) == 50


def test_query_summarize_forced_on_small_result(isolated_vault: Path) -> None:
    p = _seed(isolated_vault, "people.csv", SAMPLE)
    out = vault_csv.csv_query(p, "SELECT * FROM t", summarize=True)
    assert out["summarized"] is True
    assert "summary" in out
