"""Unified-diff parsing and optional baseline impact data."""

from __future__ import annotations

from dataclasses import dataclass, field
import gzip
import json
from pathlib import Path
import re


DECLARATION = re.compile(
    r"^\s*(?:@|(?:async\s+)?def\b|class\b|from\b|import\b|global\b|nonlocal\b)"
)
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass
class Edit:
    old: str = ""
    new: str = ""
    blob: str = ""
    removed: set[int] = field(default_factory=set)
    inserted_at: set[int] = field(default_factory=set)
    lines: list[str] = field(default_factory=list)
    additions: list[tuple[int, str]] = field(default_factory=list)
    structural: bool = False

    @property
    def paths(self):
        return {path for path in (self.old, self.new) if path}


def patch_path(raw):
    raw = raw.split("\t", 1)[0]
    if raw == "/dev/null":
        return ""
    if raw.startswith('"'):
        # Git quotes uncommon paths. Reject ambiguous escaping conservatively.
        raw = json.loads(raw)
    if not raw.startswith(("a/", "b/")):
        raise ValueError("invalid patch path")
    return raw[2:]


def parse_patch(text):
    edits = []
    edit = None
    old_line = old_left = new_left = 0
    for line in text.splitlines():
        if old_left or new_left:
            prefix = line[:1]
            if prefix in (" ", "-"):
                if prefix == "-":
                    edit.removed.add(old_line)
                    edit.lines.append(line[1:])
                old_line += 1
                old_left -= 1
            if prefix in (" ", "+"):
                if prefix == "+":
                    edit.inserted_at.add(old_line)
                    edit.lines.append(line[1:])
                    edit.additions.append((old_line, line[1:]))
                new_left -= 1
            if prefix not in (" ", "-", "+", "\\") or min(old_left, new_left) < 0:
                raise ValueError("malformed hunk")
            continue

        if line.startswith("diff --git "):
            edit = Edit()
            edits.append(edit)
            header = re.fullmatch(r"diff --git a/(.+) b/(.+)", line)
            if header:
                edit.old, edit.new = header.groups()
            continue
        if line.startswith("--- "):
            if edit is None or edit.lines:
                edit = Edit()
                edits.append(edit)
            edit.old = patch_path(line[4:])
        if edit is None:
            continue
        if line.startswith("+++ "):
            edit.new = patch_path(line[4:])
        elif line.startswith("index "):
            edit.blob = line[6:].partition("..")[0]
        elif line.startswith(("rename from ", "copy from ")):
            edit.old = line.split(" ", 2)[2]
            edit.structural = True
        elif line.startswith(("rename to ", "copy to ")):
            edit.new = line.split(" ", 2)[2]
            edit.structural = True
        elif line.startswith(("old mode ", "new mode ", "new file ", "deleted file ",
                              "Binary files ", "GIT binary patch")):
            edit.structural = True
        elif line.startswith("@@"):
            hunk = HUNK.match(line)
            if hunk is None:
                raise ValueError("invalid hunk header")
            old_line, old_count, new_count = hunk.group(1, 2, 4)
            old_line = int(old_line)
            old_left = 1 if old_count is None else int(old_count)
            new_left = 1 if new_count is None else int(new_count)
            if not old_left:
                old_line += 1

    if old_left or new_left or any(not edit.paths for edit in edits):
        raise ValueError("incomplete patch")
    if not edits and text.strip():
        raise ValueError("missing unified diff")
    return edits


class ImpactIndex:
    def __init__(self, path, tests):
        self.files = {}
        self.groups = []
        if not path.is_file():
            return
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            data = json.load(stream)
        if data["schema"] != 1:
            raise ValueError("unsupported impact index")
        by_id = {test["id"]: test["number"] for test in tests}
        if not by_id.keys() <= set(data["tests"]):
            return
        self.groups = [
            {by_id[test] for test in group if test in by_id} for group in data["groups"]
        ]
        self.files = data["files"]

    def select(self, edit):
        """Return baseline evidence and whether it can replace the fallback."""
        info = self.files.get(edit.old)
        if info is None or not edit.blob or not info["blob"].startswith(edit.blob):
            return set(), False
        if edit.structural or edit.old != edit.new:
            return self.groups[info["dependents"]], False

        if "fixture" in info:
            readers = self.groups[info["fixture"]]
            return readers, bool(readers)

        # Git emits replacement additions after the removed lines. At the end
        # of a function, the insertion cursor may point to the next scope.
        additions = [
            (line - 1 if line - 1 in edit.removed else line, added)
            for line, added in edit.additions
        ]
        changed = edit.removed | {line for line, _ in additions}
        if not changed:
            return set(), False
        body = {}
        for start, end, indent, scope_group in info["scopes"]:
            for line in changed:
                if start <= line <= end:
                    body[line] = (start, indent, scope_group)
        line_groups = info["lines"]
        tests = set()
        for line in changed:
            if str(line) in line_groups:
                tests.update(self.groups[line_groups[str(line)]])
            if line in body:
                tests.update(self.groups[body[line][2]])
        # Use the scope even when the altered line was never reached, such as
        # an uncovered branch. Keep the fallback for declarations and imports.
        confined = (
            body.keys() == changed
            and all(
                (body[line][0] < line or line in edit.removed)
                and (not added.strip() or len(added) - len(added.lstrip()) >= body[line][1])
                for line, added in additions
            )
            and not any(DECLARATION.match(line) for line in edit.lines)
        )
        if confined and tests and edit.old.startswith("sqlglot/"):
            return tests, True
        return tests | self.groups[info["dependents"]], False
