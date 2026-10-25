# batch-claim-audit

Offline, stdlib-only audit of documented grouped-handling promises in a
Python project against its own source code. It executes nothing, uses no
network, and never opens the contents of any `.env` file.

## The problem

Docs often promise a specific handling shape: work handled in groups, or
work handled strictly a single item at a time. Code that quietly does the
other thing passes every functional test — a run succeeds either way —
and the mismatch only surfaces when a user sizes throughput, quotas, or
per-item error handling around the promise. This tool checks the promise
statically, before any user has to discover it.

## Usage

```sh
pip install batch-claim-audit
batch-claim-audit /path/to/project
batch-claim-audit --json /path/to/project
batch-claim-audit --fail-on HIGH /path/to/project
batch-claim-audit --ignore "vendor/*" /path/to/project
batch-claim-audit --version
```

Or without installing, from a checkout:

```sh
PYTHONPATH=src python3 -m batch_claim_audit /path/to/project
```

Exit codes: `0` means PASS, `2` means FAIL, `1` means a usage error.
`--fail-on HIGH` makes a MEDIUM-only run exit `0` while still printing
the MEDIUM findings. `--ignore` takes a glob matched against relative
paths and is repeatable. `--verbose` prints the claims found and the
verified claims; `--json` prints the full machine-readable report.

## What it checks

Claims are read from the root `README.md` plus `docs/**/*.md` only.
Fenced code blocks are excluded from claims; inline code spans count as
prose. Sentences are normalised (lowercased, `n't` expanded, hyphens
turned into spaces) and matched with word boundaries on exact phrases.
A sentence containing a negation word suppresses grouped-family claims
on that sentence, but not single-item-family claims, because the
single-item phrases are themselves negative or single-item wordings.

Production Python source is everything except `tests/` directories,
`test_*.py` / `*_test.py` files, `fixtures/` directories, `docs/`,
`examples/`, and symlinked files. Evidence found only under tests or
fixtures is reported separately at MEDIUM severity.

## Rules

The exact claim phrases and the exact finding names are listed only in
the fenced block below, so that running this tool on its own tree
passes with no claims. In prose: a grouped promise with no grouping
evidence anywhere is a HIGH finding; the same promise with evidence
only under tests or fixtures is a MEDIUM finding; a single-item promise
with grouping evidence in production source is a HIGH finding, and with
evidence only under tests or fixtures is a MEDIUM finding. A promise
with matching production evidence is listed as verified. No promise
means PASS, even with grouping calls everywhere. A promise present but
with no production Python source at all is one HIGH finding per family.

```
Claim phrases, grouped family:
  "in batches", "batched", "batch processing",
  "processes in batches", "processed in batches",
  "batch uploads", "batch requests", "batch inserts",
  "bulk insert", "bulk update", "bulk upload", "bulk requests"

Claim phrases, single-item family:
  "one by one", "one at a time", "item by item",
  "no batching", "without batching", "not batched",
  "does not batch", "never batches",
  "processes items individually", "process items individually",
  "handles items individually", "handle items individually"

Finding names:
  documented-batched-claim-false (HIGH)
  documented-batched-claim-false-test-only (MEDIUM)
  documented-one-by-one-claim-false (HIGH)
  documented-one-by-one-claim-false-test-only (MEDIUM)

Grouping evidence table:
  - grouping helper call: batched / chunked, as a bare name or attribute
  - stepped loop: for over range() with exactly 3 arguments and a
    literal step greater than 1, or a non-literal step
  - grouped-operation call: a call whose name contains batch or bulk
    (but not exactly "batch" or "bulk" alone)
  - executemany attribute call
  - grouped-size keyword: batch_size / bulk_size with a value that is
    not the literal 1 and not None
```

## Differentiation

- pagination-claim-audit: whether results are fetched page by page from
  a source. This tool asks whether work is handled in groups per call,
  regardless of where the items came from.
- retry-claim-audit: how many times one operation is attempted. This
  tool asks how many items one operation covers.
- streaming-claim-audit: how much is read at once. This tool asks how
  many items are handled per call.
- concurrency-claim-audit: how many things run at once. A grouped call
  can still be single-threaded, and parallel calls can still handle
  items singly; the questions are independent.
- bandit / ruff: code issues, never docs promises.
- itertools grouping helpers and their documentation: libraries that
  provide grouping, never docs-vs-code truth.

## Honest limitations

- Claims are exact phrases, not semantic judgements. A paraphrase of a
  grouped promise that is not in the phrase list is not detected.
- Matching is word-boundary based: a bare `batch` word, a bare
  `batch size` phrase, and the word `unbatched` are not promises.
- Grouping evidence is name- and shape-based: a function merely named
  with batch or bulk counts even if its body is unknown, and a
  grouped-size keyword with a variable value counts even if that
  variable holds 1 at runtime. No size check is performed.
- An import of a grouping helper with no call does not count: grouping
  must be invoked to group anything. This differs deliberately from
  tools where an import alone is evidence.
- A local function, class, or parameter with the same name suppresses
  bare-name evidence in that file; attribute evidence is not suppressed
  because the receiver type is not inferred.
- Dead code counts: a grouping call in uncalled code is still evidence.
- A plain per-item loop, a single-item call, a variable or string
  literal merely named with batch, and a stepped loop with a literal
  step of 1 are not evidence.
- Only Python source is examined; other languages are out of scope.

## Development

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m build
```

## License

MIT.
