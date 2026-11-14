# abiforge

A completeness generator for C/C++ ABI and API checkers.

abiforge enumerates a formally stated space of library edits, builds every
old/new pair with real compilers, and derives each pair's ground truth
independently of any checker:

* from the C/C++ language rules and the platform ABI (SysV/Itanium layout,
  mangling, vtables, ELF symbol binding and versioning), declared per edit
  operator;
* from an execution oracle: consumer programs built against the old library
  are run against the new one, and the new header is compiled by consumers.

A checker under test then answers for each pair through an adapter, and
abiforge reports which part of the space the checker covers and where its
answer departs from the ground truth.

The goal is evidence, not anecdotes: to show, for an explicitly stated space,
that a checker detects every class of change in it, and to say exactly which
part of the language is inside that space and which is not.

## What it is made of

```text
abiforge/
├── model/        # the space: configs and toolchains, seeds, the edit model
│                 #   (edit classes from C/C++ layout rules), expectations
├── operators/    # the edit operators and their declared ground truth
│                 #   (core, elf, cxx, platform, symbols, layout)
├── run/          # build, snapshot via a checker, run consumers, reconcile,
│                 #   tiers (PR / nightly / weekly), pair reports
├── checkers/     # adapters for the checkers under test (abicheck today)
├── coverage.py   # coverage of the space and of a checker's finding kinds
├── coverage_ledger.json  # the ratchet: declared and verified coverage
└── known.py      # known checker mismatches (strict xfails with their reason)
```

## The completeness claim

"Complete" is only meaningful against a stated space. abiforge makes three
separate claims and keeps them apart:

1. **The space** (`model/space.py`, `model/edit_model.py`): its axes
   (construct, language and standard, config feature, build and consumer
   toolchain, evidence, edit class) and what is explicitly outside it. A
   model class no operator generates is a reported gap, never dropped.
2. **The generator covers the space**: every edit class has an operator
   (`coverage.py` reports the rest), and for the record-layout fragment the
   enumeration is proved complete in Lean (`formal/`, `formal/FRAGMENT.md`).
3. **The checker detects the space**: the run reports, per tier, which
   expected finding kinds the checker produced, and every disagreement is
   either fixed in the checker or recorded in `known.py`.

What is proved, what is tested and what is assumed follows
`formal/README.md` § Boundaries: the abstract space and operators can be
proved; that C/C++ text realizes an abstract edit, and that a checker is
right, are tested.

## Running it

abiforge is driven from the abicheck test suite today:

```bash
# PR tier (fixed configs, every operator x seed x compiler x header backend)
pytest tests/test_abi_edit_corpus.py -m integration

# nightly / weekly tiers (covering arrays over the config space)
ABICHECK_EDIT_CORPUS_TIER=nightly pytest tests/test_abi_edit_corpus_matrix.py -m edit_corpus_nightly

# coverage report and ledger
python -m abiforge.coverage report RESULTS_DIR
python -m abiforge.coverage ledger --write
python -m abiforge.run.report RESULTS_DIR
```

## Checking other tools

The core (space, operators, expectations, oracle, coverage) does not import
any checker. A checker plugs in through `checkers/<name>.py`, which provides
`snapshot`, `answer`, `kind_names`, `kind_catalog` and
`header_backend_problem` (see `checkers/abicheck.py`). Today the operators'
expectations are spelled in abicheck's `ChangeKind` vocabulary; evaluating a
second checker (libabigail, ABICC) needs a translation of that vocabulary
into its findings, plus its adapter.

## Boundaries

`tests/test_abiforge_boundaries.py` (in the abicheck repository) checks that
abiforge never imports `tests` or `pytest`, and that only `checkers/` imports
`abicheck`. That is what keeps it movable to its own repository.
