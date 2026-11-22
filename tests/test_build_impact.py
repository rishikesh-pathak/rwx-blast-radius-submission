"""Exercise the optional index builder against actual independent mutations."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from impact import ImpactIndex
from selector import Inventory, select_patch


BUILDER = Path(__file__).resolve().parents[1] / "tools" / "build_impact.py"


@unittest.skipUnless(
    importlib.util.find_spec("coverage") and importlib.util.find_spec("pytest"),
    "the index builder requires coverage and pytest",
)
class BuildImpactTest(unittest.TestCase):
    def test_inherited_method_and_fixture_read(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tree = root / "upstream"
            source = tree / "sqlglot" / "core.py"
            adapter = tree / "sqlglot" / "adapter.py"
            fixture = tree / "tests" / "fixtures" / "query.sql"
            test = tree / "tests" / "test_impact.py"
            source.parent.mkdir(parents=True)
            fixture.parent.mkdir(parents=True)
            source.write_text("class Base:\n    def evaluate(self, value):\n        return value + 1\n")
            adapter.write_text("from .core import Base\n\nclass Adapter(Base):\n    pass\n")
            fixture.write_text("SELECT 1\n")
            test.write_text(
                "from pathlib import Path\n"
                "from sqlglot.adapter import Adapter\n\n"
                "def test_adapter():\n"
                "    assert Adapter().evaluate(1) == 2\n\n"
                "def test_fixture():\n"
                "    assert Path('tests/fixtures/query.sql').read_text() == 'SELECT 1\\n'\n\n"
                "def test_unrelated():\n"
                "    assert True\n"
            )

            def git(*args):
                return subprocess.check_output(["git", *args], cwd=tree, text=True)

            git("init", "-q")
            git("add", ".")
            git("-c", "user.name=Test", "-c", "user.email=test@example.org", "commit", "-qm", "baseline")
            manifest = root / "manifest.json"
            index_file = root / "impact.json.gz"
            tests = [
                {"id": f"tests/test_impact.py::{name}", "number": number,
                 "file": "tests/test_impact.py", "seconds": 1}
                for number, name in enumerate(("test_adapter", "test_fixture", "test_unrelated"))
            ]
            manifest.write_text(json.dumps({"tests": tests}))
            subprocess.run(
                [sys.executable, "-B", str(BUILDER), str(tree), git("rev-parse", "HEAD").strip(),
                 str(manifest), str(index_file)],
                check=True, capture_output=True, text=True,
            )

            source.write_text(source.read_text().replace("value + 1", "value + 2"))
            fixture.write_text("SELECT 2\n")
            diff = git("diff")
            index = ImpactIndex(index_file, tests)
            self.assertEqual(select_patch(diff, Inventory(tests), index), {0, 1})

            result = subprocess.run(
                [sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider"],
                cwd=tree, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("FAILED tests/test_impact.py::test_adapter", result.stdout)
            self.assertIn("FAILED tests/test_impact.py::test_fixture", result.stdout)
            self.assertIn("2 failed, 1 passed", result.stdout)
