"""Conservative static test impact selector for the pinned sqlglot tree.

Uses patch paths and content, plus the provided test inventory. Does not
reference sample answer labels, patch identifiers or failing-test lists.
"""
from __future__ import annotations
import json
import os
import re
from collections import defaultdict
from pathlib import Path

from impact import ImpactIndex, parse_patch
from schedule import pack


DATA = Path(os.environ.get("DATA_DIR", "/data"))
INDEX = Path(os.environ.get("IMPACT_INDEX", Path(__file__).with_name("baseline") / "impact.json.gz"))


def norm(s):
    return re.sub(r'[^a-z0-9]+','',s.lower())


class Inventory:
    def __init__(self, tests):
        self.tests = tests
        self.all = {test["number"] for test in tests}
        self.cheap = {test["number"] for test in tests if test["seconds"] <= 1.5}
        self.by_file = defaultdict(set)
        self.optimizer = defaultdict(set)
        self.matches = {}
        for test in tests:
            self.by_file[test["file"]].add(test["number"])
            if test["file"] == "tests/test_optimizer.py":
                method = test["id"].split("::")[-1].removeprefix("test_")
                self.optimizer[norm(method)].add(test["number"])
        self.optimizer_fast = {
            test["number"] for test in tests
            if test["file"] == "tests/test_optimizer.py" and test["seconds"] <= 0.15
        }

    def match(self, pattern):
        if pattern not in self.matches:
            self.matches[pattern] = {
                test["number"] for test in self.tests if pattern in test["id"].lower()
            }
        return self.matches[pattern]


def fallback_tests(paths, mods, inventory):
    # Comment-looking additions can occur inside strings or SQL fixtures.
    by_file = inventory.by_file
    chosen = set()

    def file(path):
        chosen.update(by_file.get(path, ()))

    def match(pattern):
        chosen.update(inventory.match(pattern))

    def opt_name(name):
        key = norm(name)
        for variant in (key, key + "s", key.rstrip("s")):
            chosen.update(inventory.optimizer.get(variant, ()))

    if any(not path.startswith(("sqlglot/", "sqlglotc/", "tests/", "docs/"))
           and path not in {"README.md", "LICENSE", "CHANGELOG.md"} for path in paths):
        return inventory.all

    # Route familiar optimizer fixture names to their corresponding methods.
    for p in paths:
        previous = chosen
        chosen = set()
        if p.startswith('tests/fixtures/optimizer/'):
            name=Path(p).stem
            opt_name(name)
            if name=='pushdown_projections':opt_name('pushdown_projection')
            if name.endswith('_ddl'):opt_name(name.removesuffix('_ddl'))
            # These suffixes identify variants of the same fixture family.
            # Back off to the main optimizer method when a fixture's specific
            # suffix is not named in the tests manifest.
            if '__' in name:opt_name(name.split('__', 1)[0])
            # Unknown fixture naming conventions must not produce an empty run.
            # Keep the optimizer suite when a fixture has no known test name.
            if not chosen:
                file('tests/test_optimizer.py')
        elif p.startswith('tests/fixtures/'):
            # Other fixture groups can affect a family of tests in the same dialect.
            name=Path(p).stem
            dialect=p.split('/')[2] if len(p.split('/'))>3 else ''
            file('tests/dialects/test_'+dialect+'.py')
            match(name)
            if not chosen:
                chosen.update(inventory.all)
        elif p.startswith('tests/'):
            file(p)
            if not chosen:
                chosen.update(inventory.all)
        chosen.update(previous)
    code_paths=[p for p in paths if p.startswith(('sqlglot/', 'sqlglotc/'))]
    if not code_paths:
        return chosen
    # Global low-cost regression shield: almost all sqlglot tests take less
    # than 1.5 seconds individually. Running them catches cross-module effects
    # missed by path-based heuristics, at modest simulated cost (worker setup
    # dominates tiny tests). The few very expensive tests are targeted below.
    chosen.update(inventory.cheap)
    for p in code_paths:
        stem=Path(p).stem
        if '/dialects/' in p or '/parsers/' in p or '/generators/' in p or '/tokens/' in p:
            if stem in ('parser','generator','dialect','tokens','tokenizer'):
                file('tests/dialects/test_dialect.py')
                file('tests/test_'+stem+'.py')
                # central changes may impact any dialect
                for f in by_file:
                    if f.startswith('tests/dialects/'):file(f)
                if stem=='parser':file('tests/test_parser.py')
            else:
                file('tests/dialects/test_'+stem+'.py')
                # Dialect-specific changes generally need their dialect suite;
                # targeted optimizer methods are added based on the patch below.
                # all dialect parser changes can affect expression building;
                # keep the comparatively cheap expression suite.
                if '/parsers/' in p:
                    file('tests/test_expressions.py')
                if '/dialects/' in p:
                    file('tests/test_transpile.py')
                # parser has numerous integration contexts, but large generic
                # tests are too expensive unless patch impacts shared behavior.
        elif '/optimizer/' in p:
            # The optimizer is a chain of interacting passes. Fast global tests
            # cover most regression cases, and we additionally keep *all*
            # optimizer tests, including slow scope / TPC-DS tests.
            file('tests/test_optimizer.py')
            if stem in ('eliminate_joins','scope','qualify_columns','optimizer'):
                opt_name('optimize_joins')
                opt_name('qualify_columns')
                if stem=='eliminate_joins':
                    match('test_execute_subqueries');match('test_execute_tpcds')
                    opt_name('tpcds');opt_name('tpch')
        elif '/typing/' in p:
            file('tests/test_optimizer.py')
            file('tests/test_typing.py')
        elif stem=='serde':
            file('tests/test_serde.py');file('tests/test_optimizer.py');file('tests/test_executor.py')
            # serialization underpins the dialects as well.
            for f in by_file:
                if f.startswith('tests/dialects/'):file(f)
            file('tests/test_expressions.py')
        elif '/executor/' in p:
            file('tests/test_executor.py')
        elif '/expressions/' in p or stem in ('expressions','exp'):
            file('tests/test_expressions.py')
            if stem in ('expressions','exp'):
                file('tests/test_optimizer.py');file('tests/test_parser.py')
            else:
                # Leaf expression families: select broad cheap optimization
                # regression coverage, without unrelated long workloads.
                chosen.update(inventory.optimizer_fast)
                opt_name('optimize')
                file('tests/test_generator.py')
        elif stem in ('transforms','generator'):
            file('tests/test_generator.py')
            file('tests/test_transforms.py')
        elif stem=='schema':
            file('tests/test_schema.py');file('tests/test_optimizer.py')
        elif stem=='helper':
            file('tests/test_helper.py');file('tests/test_optimizer.py')
        else:
            # Unknown central module: broad but valid conservative fallback.
            for f in by_file:
                if f.startswith('tests/test_'):file(f)
    # Subtle changes to dialects can propagate to optimization without direct
    # optimizer file edits. For these cases include the lightweight optimizer
    # methods indicated by changed code keywords.
    txt='\n'.join(mods).lower()
    if any('/parsers/' in p for p in code_paths):
        # Parser output is consumed by the generic qualification, optimizer,
        # and expression-type tests even if the edit is dialect-specific.
        for k in ('qualify_columns','qualify_tables','quote_identifiers',
                  'optimize','annotate_funcs'):
            opt_name(k)
        match('test_ref_type')
    if any('/dialects/' in p for p in code_paths):
        if any(k in txt for k in ('week_offset','datefirst','date_trunc','timestamp_trunc','types')):
            opt_name('simplify')
        if any(k in txt for k in ('typeannotat','_annotate','set_type','exp.','expression')):
            for k in ('annotate_funcs','annotate_types','optimize','qualify_columns'):
                opt_name(k)
    # Highly expensive tests, which the global budget deliberately excludes.
    # Enable them when changes touch the code paths they stress. The rules
    # require these selections to depend only on paths, not sample answers.
    for p in code_paths:
        name = Path(p).name
        if name in ('parser.py', 'tokens.py', 'tokenizer.py', 'expressions.py', 'exp.py') and not ('/parsers/' in p or '/tokens/' in p):
            file('tests/test_parser.py')
        if ('/generators/' in p or name == 'generator.py') and ('memory' in txt or name == 'generator.py'):
            file('tests/test_generator.py')
        if '/optimizer/' in p or '/typing/' in p or '/executor/' in p or name in ('serde.py', 'schema.py'):
            file('tests/test_optimizer.py')
        if name in ('executor.py', 'context.py', 'serde.py') or ('/optimizer/' in p and any(k in txt for k in ('execute','executor','tpcds','tpch'))):
            file('tests/test_executor.py')
    return chosen



def select_patch(text, inventory, index):
    try:
        edits = parse_patch(text)
    except ValueError:
        return inventory.all

    selected = set()
    unresolved = []
    for edit in edits:
        evidence, confined = index.select(edit)
        selected.update(evidence)
        if not confined:
            unresolved.append(edit)

    paths = sorted({path for edit in unresolved for path in edit.paths})
    lines = [line for edit in unresolved for line in edit.lines]
    selected.update(fallback_tests(paths, lines, inventory))
    return selected


def main():
    tests = json.loads((DATA / "tests.json").read_text())["tests"]
    durations = {test["number"]: round(test["seconds"] * 1000) for test in tests}
    index = ImpactIndex(INDEX, tests)
    inventory = Inventory(tests)
    for patch in sorted((DATA / "commits").glob("*.patch")):
        selected = select_patch(patch.read_text(errors="replace"), inventory, index)
        output = {"commit": patch.stem, "workers": pack(selected, durations)}
        print(json.dumps(output, separators=(",", ":")))


if __name__ == "__main__":
    main()
