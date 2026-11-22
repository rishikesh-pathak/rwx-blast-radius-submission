# Blast Radius — Static Impact Selector (v2)

Entry point: `selector.py` (Python standard library only). Build: `Dockerfile` at repository root. The bot reads `/data/commits/*.patch` and `/data/tests.json`, prints exactly one JSON line per commit, and uses at most eight simulated workers. No external network or file writes are needed at runtime.

## Strategy

- Parse changed paths and diff content. Pure standalone Python comment/whitespace-only changes are treated as no-op only when all changed lines meet this strict condition. Other edits are treated as potentially relevant.
- Route optimizer fixtures to matching optimizer test methods; include specialized handling for plural names and suffixes.
- Route dialect, parser, optimizer, typing, expression, executor and serialization changes to affected test families. For individual optimizer passes and leaf expression modules, include focused regression tests plus cheap cross-pass tests rather than all expensive unrelated optimizer tests. Keep additional generic optimizer integration tests when parser changes might alter parse trees.
- Compute recorded milliseconds from the given test manifest; greedily balance longer tests first and choose the best worker count from 1–8 inclusive after charging 2 seconds per occupied worker.

This is a heuristic, not a perfect impact oracle. It may miss previously unseen regressions, including semantic behavior caused by comments, dependency changes outside directly selected test families, and test changes with unusual filenames. The hidden scorer decides real quality.

## Test on Windows without Docker

Double-click `TEST_ON_WINDOWS.bat`. Python 3.9+ required. It uses the 30 official sample commits and official `score.py` and prints the simulated total and any missed sample commits. It does **not** measure container CPU seconds.

Manual equivalent (PowerShell in this folder):

```powershell
$env:DATA_DIR = (Resolve-Path .\sample).Path
python selector.py > output.jsonl
python score.py sample sample/answers.json output.jsonl
```

## Test with Docker

On Linux/macOS with Docker and Make:

```bash
make sample
make check
```

`make sample` executes with the official container restrictions and evaluates its output against the sample answer. Competition runs use `/data` directly. No sample answers, hardcoded patch IDs, or lookups from patch classes to known failing-test labels are loaded by the program.

## Submit

Upload source files to your **own new public GitHub repository** with `Dockerfile` at its top level. Submit the repository URL and the latest commit hash in the RWX Blast Radius competition page. Do not submit the ZIP as the repository. The repository must stay within the 20 MB limit.

This local run scored **166.946 simulated seconds and zero misses on the 30 public sample patches**. This does not predict the hidden practice or final score.
