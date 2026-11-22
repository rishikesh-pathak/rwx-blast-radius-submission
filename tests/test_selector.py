import gzip
import json
from pathlib import Path
import tempfile
import unittest

from impact import ImpactIndex, parse_patch
from schedule import pack
from selector import Inventory, select_patch


def patch(path, hunk, blob="1234567"):
    return f"diff --git a/{path} b/{path}\nindex {blob}..7654321 100644\n--- a/{path}\n+++ b/{path}\n{hunk}"


class SelectorTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "index.gz"
        self.tests = [
            {"id": f"{file}::test_demo", "number": n, "file": file, "seconds": 1}
            for n, file in enumerate(("tests/test_parent.py", "tests/test_other.py"))
        ]

        self.inventory = Inventory(self.tests)

    def index(self):
        return ImpactIndex(self.path, self.tests)

    def write_index(self):
        ids = [test["id"] for test in self.tests]
        data = {
            "schema": 1, "tests": ids, "groups": [ids[:1], ids],
            "files": {
                "sqlglot/demo.py": {
                    "blob": "1234567890", "dependents": 1,
                    "scopes": [[8, 15, 4, 0]], "lines": {"9": 0},
                },
                "tests/fixtures/example.sql": {
                    "blob": "1234567890", "fixture": 0, "dependents": 0,
                },
            },
        }
        self.path.write_bytes(gzip.compress(json.dumps(data).encode()))

    def test_hunk_coordinates_and_header_like_payload(self):
        edit, = parse_patch(patch("sqlglot/demo.py", "@@ -8,2 +8,2 @@\n keep\n---old\n+++new\n"))
        self.assertEqual(edit.removed, {9})
        self.assertEqual(edit.inserted_at, {10})
        self.assertEqual(edit.lines, ["--old", "++new"])

    def test_rename_and_deletion(self):
        edit, = parse_patch(
            "diff --git a/sqlglot/a.py b/sqlglot/b.py\n"
            "similarity index 100%\nrename from sqlglot/a.py\nrename to sqlglot/b.py\n"
        )
        self.assertTrue(edit.structural)
        self.assertEqual(edit.paths, {"sqlglot/a.py", "sqlglot/b.py"})
        edit, = parse_patch(patch("sqlglot/a.py", "@@ -1 +0,0 @@\n-gone\n").replace(
            "+++ b/sqlglot/a.py", "+++ /dev/null"))
        self.assertEqual(edit.paths, {"sqlglot/a.py"})

    def test_scope_covers_new_branch_and_fixture_edges(self):
        self.write_index()
        addition = patch("sqlglot/demo.py", "@@ -9,0 +10,2 @@\n+    if new_branch:\n+        return 0\n")
        fixture = patch("tests/fixtures/example.sql", "@@ -1 +1 @@\n-SELECT 1\n+SELECT 2\n")
        self.assertEqual(select_patch(addition + fixture, self.inventory, self.index()), {0})

    def test_import_signature_and_blob_mismatch_keep_fallback(self):
        self.write_index()
        for change in (
            patch("sqlglot/demo.py", "@@ -9,0 +10 @@\n+    import plugin\n"),
            patch("sqlglot/demo.py", "@@ -9,0 +10 @@\n+NEW_CONSTANT = 1\n"),
            patch("sqlglot/demo.py", "@@ -9,0 +10 @@\n+    flag = True\n", blob="9876543"),
        ):
            self.assertEqual(select_patch(change, self.inventory, self.index()), {0, 1})

    def test_uncertain_changes_keep_coverage(self):
        for text in (
            "diff --git a/unreadable",
            patch("sqlglot/demo.py", "@@ -9,0 +10 @@\n+    # could be string data\n"),
            patch("tests/shared.py", "@@ -1 +1 @@\n-old\n+new\n"),
        ):
            self.assertEqual(select_patch(text, self.inventory, self.index()), {0, 1})

    def test_worker_startup_cost_and_no_duplicates(self):
        self.assertEqual(pack([0, 1, 0], {0: 2000, 1: 2000}), [[0, 1]])
        self.assertEqual(pack([0, 1, 0], {0: 10000, 1: 9000}), [[0], [1]])
        jobs = pack(range(100), dict.fromkeys(range(100), 5000))
        self.assertEqual(len(jobs), 8)
        self.assertEqual(sorted(job for worker in jobs for job in worker), list(range(100)))


if __name__ == "__main__":
    unittest.main()
