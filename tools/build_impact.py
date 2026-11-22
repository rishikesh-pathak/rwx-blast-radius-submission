"""Collect per-test impact data from an explicitly pinned, clean SQLGlot checkout."""

from __future__ import annotations

import argparse
import ast
from collections import defaultdict
import gzip
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import coverage
import pytest


FIXTURES = "tests/fixtures/"


class Collector:
    def __init__(self, root, tracer, wanted):
        self.root = root
        self.tracer = tracer
        self.wanted = wanted
        self.passed = set()
        self.failed = set()
        self.active = ""
        self.readers = defaultdict(set)
        self.groups = defaultdict(set)

    def audit(self, event, args):
        if event != "open" or not isinstance(args[0], (str, bytes)):
            return
        path = os.path.relpath(os.fsdecode(args[0]), self.root)
        if path.startswith(FIXTURES):
            self.readers[path].add(self.active)

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_protocol(self, item, nextitem):
        self.active = item.nodeid
        self.tracer.switch_context(self.active)
        try:
            return (yield)
        finally:
            self.active = ""
            self.tracer.switch_context("")

    @pytest.hookimpl(wrapper=True)
    def pytest_fixture_setup(self, fixturedef, request):
        previous = self.active
        if previous and fixturedef.scope != "function":
            self.active = f"fixture:{fixturedef.scope}:{request.node.nodeid}"
            self.tracer.switch_context(self.active)
        try:
            return (yield)
        finally:
            if self.active != previous:
                self.active = previous
                self.tracer.switch_context(previous)

    def pytest_collection_modifyitems(self, items):
        for item in items:
            if item.nodeid not in self.wanted:
                continue
            for node in item.listchain():
                self.groups[node.nodeid].add(item.nodeid)
        items[:] = [item for item in items if item.nodeid in self.wanted]

    def pytest_runtest_logreport(self, report):
        if report.when == "call" and report.passed:
            self.passed.add(report.nodeid)
        if report.failed or report.skipped:
            self.failed.add(report.nodeid)

    def expand(self, contexts):
        tests = set()
        for context in contexts:
            if not context:
                return self.wanted
            if context.startswith("fixture:"):
                node = context.split(":", 2)[2]
                tests.update(self.groups.get(node, self.wanted))
            elif context in self.wanted:
                tests.add(context)
        return tests


def module_name(path):
    return path.removesuffix(".py").replace("/", ".").removesuffix(".__init__")


def dependencies(tree, path, modules):
    package = module_name(path)
    if not path.endswith("/__init__.py"):
        package = package.rpartition(".")[0]
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            name = node.module or ""
            if node.level:
                try:
                    name = importlib.util.resolve_name("." * node.level + name, package)
                except ImportError:
                    continue
            imported.add(name)
            imported.update(f"{name}.{alias.name}" for alias in node.names)
    for name in imported:
        while name:
            if name in modules:
                yield modules[name]
            name = name.rpartition(".")[0]


def function_scopes(tree, lines, expand, group):
    scopes = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        start = node.body[0].lineno
        # An inline body shares a line with the signature.
        if start <= node.lineno:
            continue
        contexts = set()
        for line in range(start, node.end_lineno + 1):
            contexts.update(lines.get(line, ()))
        scopes.append([start, node.end_lineno, node.body[0].col_offset, group(expand(contexts))])
    # Inner scopes take precedence when overlapping an outer function.
    return sorted(scopes, key=lambda scope: (scope[0], -scope[1]))


def make_index(root, revision, blobs, collector, traced):
    test_files = defaultdict(set)
    for test in collector.wanted:
        test_files[test.split("::", 1)[0]].add(test)
    group_ids = {}

    def group(tests):
        key = tuple(sorted(tests))
        return group_ids.setdefault(key, len(group_ids))

    trees = {
        p: ast.parse((root / p).read_bytes(), filename=p)
        for p in blobs if p.endswith(".py")
    }
    modules = {module_name(p): p for p in trees}
    reverse = defaultdict(set)
    for path, tree in trees.items():
        for dependency in dependencies(tree, path, modules):
            reverse[dependency].add(path)

    files = {}
    for path, blob in blobs.items():
        if path.startswith(FIXTURES):
            readers = collector.expand(collector.readers[path])
            files[path] = {"blob": blob, "fixture": group(readers), "dependents": group(readers)}
            continue
        if path not in trees:
            continue
        related = set()
        visited = {path}
        pending = [path]
        while pending:
            current = pending.pop()
            related.update(test_files[current])
            new = reverse[current] - visited
            visited.update(new)
            pending.extend(new)
        contexts = traced.contexts_by_lineno(str(root / path))
        lines = {str(line): group(collector.expand(tests)) for line, tests in contexts.items()}
        for tests in contexts.values():
            related.update(collector.expand(tests))
        files[path] = {
            "blob": blob,
            "dependents": group(related),
            "lines": lines,
            "scopes": function_scopes(trees[path], contexts, collector.expand, group),
        }
    return {
        "schema": 1,
        "revision": revision,
        "tests": sorted(collector.wanted),
        "groups": list(group_ids),
        "files": files,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkout", type=Path)
    parser.add_argument("official_pin", help="Full SQLGlot commit SHA from the rules")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    root = args.checkout.resolve()
    output = args.output.resolve()
    wanted = {test["id"] for test in json.loads(args.manifest.read_text())["tests"]}

    def git(*arguments):
        return subprocess.check_output(["git", "-C", root, *arguments], text=True)

    revision = git("rev-parse", "HEAD").strip()
    if revision != args.official_pin or git("status", "--porcelain"):
        parser.error("checkout must be clean and at the exact supplied official pin")
    if not wanted:
        parser.error("test manifest is empty")
    blobs = {}
    for record in git("ls-tree", "-r", "-z", revision, "--", "sqlglot", "tests").split("\0"):
        if record:
            metadata, path = record.split("\t", 1)
            _, kind, blob = metadata.split()
            if kind == "blob" and (path.endswith(".py") or path.startswith(FIXTURES)):
                blobs[path] = blob

    os.chdir(root)
    sys.path.insert(0, str(root))
    tracer = coverage.Coverage(source=[str(root / "sqlglot"), str(root / "tests")], config_file=False)
    collector = Collector(root, tracer, wanted)
    sys.addaudithook(collector.audit)
    tracer.start()
    try:
        result = pytest.main(["-q", "-p", "no:cacheprovider", "tests"], plugins=[collector])
    finally:
        tracer.stop()
    missing = wanted - (collector.passed - collector.failed)
    if result or missing:
        print(f"Coverage rejected: {len(missing)} unresolved manifest tests", file=sys.stderr)
        for test in sorted(missing)[:20]:
            print(test, file=sys.stderr)
        return 1
    data = make_index(root, revision, blobs, collector, tracer.get_data())
    encoded = gzip.compress(json.dumps(data, separators=(",", ":")).encode(), mtime=0)
    if len(encoded) >= 20_000_000:
        parser.error("compressed index alone exceeds the repository limit")
    output.write_bytes(encoded)
    print(f"{len(wanted)} tests indexed, {len(encoded)} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
