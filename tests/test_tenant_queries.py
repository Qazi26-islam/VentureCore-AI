import ast
import re
import unittest
from pathlib import Path


DOMAIN_TABLES = (
    "research_jobs",
    "follow_up_messages",
    "company_profiles",
    "data_uploads",
    "suppliers",
    "products",
    "inventory_transactions",
    "customers",
    "sales_orders",
    "finance_transactions",
)


class OrganizationQueryTests(unittest.TestCase):
    def test_every_domain_select_filters_by_organization(self):
        root = Path(__file__).resolve().parents[1]
        failures = []
        for relative_path in ("backend/api/research.py", "backend/db.py"):
            source = (root / relative_path).read_text(encoding="utf-8")
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                    continue
                sql = " ".join(node.value.split()).lower()
                if not sql.startswith("select") and " select " not in sql:
                    continue
                touched = [table for table in DOMAIN_TABLES if re.search(rf"\b(from|join)\s+{table}\b", sql)]
                if touched and "organization_id" not in sql:
                    failures.append(f"{relative_path}:{node.lineno} reads {', '.join(touched)} without organization_id")
        self.assertEqual(failures, [], "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
