"""Read-only DB viewer.

    docker exec gm-backend python -m app.dbview            # 요약
    docker exec gm-backend python -m app.dbview servers    # 테이블 전체
    docker exec gm-backend python -m app.dbview --sql "SELECT ..."

Read-only on purpose: the connection is opened in SQLite's read-only mode and
non-SELECT statements are refused, so an accidental UPDATE against live
customer data cannot happen from here. Use the admin page to change anything.
"""

import argparse
import sqlite3
import sys

from app.config import settings

# Never print these, even when dumping a whole row.
SECRET_COLUMNS = {"rcon_password", "password_hash", "token", "session_token"}

SUMMARY_COLUMNS = {
    "servers": [
        "nickname", "tier", "status", "mc_type", "mc_version",
        "payment_status", "payment_code", "price_krw", "game_port",
        "container_name", "created_at",
    ],
    "users": ["email", "created_at"],
    "applications": ["nickname", "email", "tier", "status", "created_at"],
    "auth_tokens": ["user_id", "created_at", "expires_at"],
}


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{settings.db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def mask(column: str, value) -> str:
    if value is None:
        return "-"
    if column in SECRET_COLUMNS:
        return f"<{column} 숨김>"
    return str(value)


def print_rows(rows: list[sqlite3.Row], columns: list[str] | None = None) -> None:
    if not rows:
        print("  (없음)")
        return

    columns = columns or list(rows[0].keys())
    columns = [c for c in columns if c in rows[0].keys()]
    table = [[mask(c, r[c]) for c in columns] for r in rows]
    widths = [
        min(max(len(c), *(len(row[i]) for row in table)), 42)
        for i, c in enumerate(columns)
    ]

    def line(cells):
        return "  " + "  ".join(str(x)[:w].ljust(w) for x, w in zip(cells, widths))

    print(line(columns))
    print("  " + "  ".join("-" * w for w in widths))
    for row in table:
        print(line(row))


def tables(conn) -> list[str]:
    return sorted(
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    )


def summary(conn) -> None:
    print(f"DB: {settings.db_path}\n")
    for table in tables(conn):
        rows = conn.execute(f"SELECT * FROM {table}").fetchall()
        print(f"[{table}]  {len(rows)}행")
        print_rows(rows, SUMMARY_COLUMNS.get(table))
        print()


def main() -> int:
    parser = argparse.ArgumentParser(description="Glistering Melon DB 보기 (읽기 전용)")
    parser.add_argument("table", nargs="?", help="테이블 이름 (생략하면 전체 요약)")
    parser.add_argument("--sql", help="직접 SELECT 실행")
    parser.add_argument("--all-columns", action="store_true", help="요약 대신 모든 컬럼")
    args = parser.parse_args()

    conn = connect()
    try:
        if args.sql:
            if not args.sql.lstrip().lower().startswith("select"):
                print("SELECT 문만 실행할 수 있습니다.", file=sys.stderr)
                return 1
            print_rows(conn.execute(args.sql).fetchall())
        elif args.table:
            if args.table not in tables(conn):
                print(f"'{args.table}' 테이블이 없습니다. 있는 것: {tables(conn)}", file=sys.stderr)
                return 1
            rows = conn.execute(f"SELECT * FROM {args.table}").fetchall()
            print(f"[{args.table}]  {len(rows)}행")
            print_rows(rows, None if args.all_columns else SUMMARY_COLUMNS.get(args.table))
        else:
            summary(conn)
        return 0
    except sqlite3.Error as exc:
        print(f"SQL 오류: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
