#!/usr/bin/env python3
"""table-peek.py — print only the part of a downloaded data table that a question needs.

Usage:
    table-peek.py <file.csv> [--columns A,B] [--where COLUMN=TEXT] [--limit N]

Prints the column names, the row count, and up to --limit rows (default 10),
keeping only --columns and rows whose COLUMN contains TEXT. Reads UTF-8 and
the legacy Korean encoding (CP949) that public data portals often use.

Exit codes:
    0  printed the table summary
    1  the file could not be read or a named column does not exist
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path


ENCODINGS = ("utf-8-sig", "cp949")
DEFAULT_LIMIT = 10
EXIT_READ_FAILURE = 1


def read_rows(path: Path) -> list[dict[str, str]]:
    for encoding in ENCODINGS:
        try:
            with path.open(encoding=encoding, newline="") as file:
                return list(csv.DictReader(file))
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("table-peek", b"", 0, 1, f"not {' or '.join(ENCODINGS)}")


def parse_condition(condition: str | None) -> tuple[str, str] | None:
    if condition:
        column, _, text = condition.partition("=")
        return column.strip(), text.strip()
    else:
        return None


def select_rows(rows: list[dict[str, str]], condition: tuple[str, str] | None) -> list[dict[str, str]]:
    if condition:
        column, text = condition
        return [row for row in rows if text in (row.get(column) or "")]
    else:
        return rows


def find_missing_columns(header: list[str], columns: list[str], condition: tuple[str, str] | None) -> list[str]:
    wanted = columns + ([condition[0]] if condition else [])

    return [column for column in wanted if column not in header]


def main() -> int:
    parser = argparse.ArgumentParser(description="Print the part of a CSV table that a question needs.")
    parser.add_argument("file", type=Path)
    parser.add_argument("--columns", default="", help="comma-separated column names to keep")
    parser.add_argument("--where", help="COLUMN=TEXT keeps rows whose COLUMN contains TEXT")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    arguments = parser.parse_args()
    columns = [column.strip() for column in arguments.columns.split(",") if column.strip()]
    condition = parse_condition(arguments.where)

    try:
        rows = read_rows(arguments.file)
        header = list(rows[0].keys()) if rows else []
        missing = find_missing_columns(header, columns, condition)
        if missing:
            print(f"Unknown columns: {', '.join(missing)}. Columns: {', '.join(header)}", file=sys.stderr)
            return EXIT_READ_FAILURE
        else:
            selected = select_rows(rows, condition)
            shown = [{column: row[column] for column in (columns or header)} for row in selected[: arguments.limit]]
            summary = {"columns": header, "rows": len(rows), "matching": len(selected), "shown": shown}
            print(json.dumps(summary, ensure_ascii=False, indent=1))
            return 0
    except (OSError, UnicodeDecodeError, csv.Error) as error:
        print(f"Could not read {arguments.file}: {error}", file=sys.stderr)
        return EXIT_READ_FAILURE


if __name__ == "__main__":
    sys.exit(main())
