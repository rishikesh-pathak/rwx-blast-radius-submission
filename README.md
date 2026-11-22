# RWX Blast Radius test selector

The entry point reads the test manifest and unified patches from `/data` and
emits one JSON line per patch. The submission image uses Python's standard
library and the digest-pinned Linux/amd64 base in the root Dockerfile.
Runtime requires no network, packages, or writable application directory.

## Selection and scheduling

- The patch parser tracks old-file coordinates, replacements, insertion
  boundaries, renames, deletions, and structural changes.
- An optional baseline index maps test IDs to covered lines, enclosing
  function bodies, fixture reads, and dependent modules. Coverage captures
  execution through shared helpers, inherited methods, and dynamic dispatch.
  An AST import graph supplies reverse dependencies for changes that cannot be
  confined to a function body.
- The selector checks baseline blob IDs before using coverage. A confined
  function-body edit uses the function's coverage as well as the changed
  lines, allowing for previously uncovered branches. Structural changes
  retain broader selection. Missing evidence falls back to component rules,
  with broad regression coverage for source changes and a full run for
  unrecognized patches or potentially global changes. Comment-looking lines
  can occur inside fixtures or strings and receive normal impact analysis.
- The scheduler tries longest-processing-time-first packing for up to eight
  workers. It minimizes the best observed makespan plus two seconds per
  worker, skips worker counts that cannot improve the current result, and
  deduplicates tests.

## Build the optional impact index

First obtain the full SQLGlot SHA directly from the official competition
rules and verify that the checkout is clean and at that exact revision.
Install the test dependencies required by that SQLGlot revision, plus
`coverage==7.10.7` and `pytest==8.4.2`, in a development environment.

```bash
python -B tools/build_impact.py /path/to/sqlglot "$OFFICIAL_PIN" \
  sample/tests.json baseline/impact.json.gz
```

The collector runs the manifest tests against the unchanged baseline and
rejects an incomplete or failed run. It records coverage by test ID, so
runtime test numbering can differ. It reads the test inventory and does
not consume public patches or answer files. Verify the resulting artifact
against public and independent cases, and check the total repository size
against the 20 MB limit before committing it. The Dockerfile packages the
index automatically when present.

The current branch ships without a baseline index. The rules URL returned a
proxy 403 in the development environment, and multiple upstream revisions
match the public preimage blob prefixes. The official pin needs confirmation
before using an upstream-generated index. Coverage also has limits when
behavior depends on process-level side effects, native code, or interactions
between edits.

## Validate

```bash
python -B -m unittest discover -s tests -v
(
  set -eu
  scratch=$(mktemp -d)
  trap 'rm -rf -- "$scratch"' EXIT
  cp -R sample "$scratch/data"
  chmod -R a+rX "$scratch/data"
  make sample check SAMPLE="$scratch/data" OUTPUT="$scratch/output.jsonl"
)
```

The integration test for index generation requires the two development
packages. The temporary fixture copy lets Docker's unprivileged user read
the data on restrictive cloud checkouts and keeps the tracked
`output.jsonl` intact.

| Linux/amd64 selector | Public patches | Misses | Simulated seconds | Median selector CPU |
| --- | ---: | ---: | ---: | ---: |
| Baseline `7e1469b` | 30 | 0 | 418.462 | 0.247 s |
| Coverage-capable selector with fallback | 30 | 0 | 418.462 | 0.198 s |

CPU is the median of five local Docker runs, measured as selector process
user plus system time under the Makefile's resource restrictions. The
independent integration test mutated a shared inherited method and a SQL
fixture. The index selected both tests that then failed and excluded the
unrelated passing test. Hidden-set accuracy and score remain unmeasured.

## Submit

Push the development commit to the public repository. Enter the repository
URL and the full SHA from `git rev-parse HEAD` on the RWX Blast Radius
competition page. The submission uses the root Dockerfile at that SHA.
The known official best remains on its original commit.
