from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any, Optional

import backend.db as database
from backend.db import DEMO_ORGANIZATION_ID, get_connection, get_demo_user_id
from backend.workers import get_or_create_briefing


def regenerate_demo_briefing(as_of: Optional[date] = None) -> dict[str, Any]:
    briefing_date = as_of or date.today()
    connection = get_connection()
    connection.execute(
        "DELETE FROM briefing_cache WHERE organization_id = ?",
        (DEMO_ORGANIZATION_ID,),
    )
    connection.commit()
    connection.close()
    return get_or_create_briefing(
        DEMO_ORGANIZATION_ID,
        get_demo_user_id(),
        briefing_date.isoformat(),
        briefing_date,
    )


def stored_demo_briefing() -> Optional[dict[str, Any]]:
    connection = get_connection()
    row = connection.execute(
        """SELECT id, period, subject, content_json, created_at
             FROM briefing_cache
            WHERE organization_id = ?
            ORDER BY created_at DESC, id DESC LIMIT 1""",
        (DEMO_ORGANIZATION_ID,),
    ).fetchone()
    connection.close()
    if row is None:
        return None
    result = dict(row)
    result["content"] = json.loads(result.pop("content_json"))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Regenerate the stored public demo briefing")
    parser.add_argument("--database", default=str(database.DB_PATH))
    parser.add_argument("--as-of", default=None, help="Briefing date in YYYY-MM-DD format")
    args = parser.parse_args()
    database.DB_PATH = Path(args.database)
    database.init_db()
    briefing = regenerate_demo_briefing(date.fromisoformat(args.as_of) if args.as_of else None)
    print(f"Regenerated demo briefing {briefing['id']} for {briefing['period']}")


if __name__ == "__main__":
    main()
