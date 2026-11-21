# CLAUDE.md — `abiforge/`

abiforge is a standalone completeness generator for C/C++ ABI/API checkers:
it builds old/new library pairs over a stated edit space and judges a checker
against ground truth taken from the language rules and an execution oracle.
Purpose and claims: `README.md`.

## Boundaries (enforced by `tests/test_abiforge_boundaries.py`)

- abiforge imports neither `tests` nor `pytest`; pytest glue lives in
  `tests/_abiforge_pytest.py`.
- Only `checkers/` imports `abicheck`. Core code reaches the checker through
  `checkers/abicheck.py` (`snapshot`, `answer`, `kind_names`, `kind_catalog`,
  `header_backend_problem`).
- Third-party tools have their own adapters in `checkers/`: `abidiff.py`
  (libabigail; exit-status bits plus report text), `abicc.py` (ABI Dumper ->
  ABICC on dumps, and ABICC on two input XML descriptors; binary and source
  output reports read apart), `abicheck_cli.py` (the `abicheck compare`
  command), all run through `checkers/tools.py`. E16's competitor lane
  (`formal/experiments/record_edit_competitors.py`) uses the same adapters.

## Ground truth

- Exact rows (`model/exact.py`, `EditOp.exact`) come from an independent
  model checked against the compiler, never from a checker. A scope names
  which kinds and entities an assertion judges; rows the configuration's
  evidence cannot establish become unknown (`evidence-limited`), never pass.
- An expectation comes from the edit's ABI meaning, never from a checker's
  answer. An `oracle-conflict` cell means the expectation is wrong.
- A checker disagreement is fixed in the checker, or recorded in `known.py`
  as a strict xfail with its `docs/contribute/known-gaps.md` row.
- Catch every edit at the cheapest evidence level that observes it: stripped
  binary, then +DWARF, then stripped binary + headers (the primary cell), then
  build data, then sources. A header-visible fact missed in that primary cell
  is a checker defect, never a reason to raise the level. When choosing or
  changing an operator's evidence level, read
  `docs/contribute/plans/abicheck-abiforge-completeness.md` §7.1.
- `requires(config)` reads only the config, never the host. A host
  capability (CPU, libstdc++, a flag the toolchain may refuse) is an `EditOp`
  field (`host_arch`, `needs_libstdcxx`, `probe_flags`) that
  `run/toolchains.capability_gap` turns into an *unavailable* cell.

## Where things live

- **Operators** and their ground truth: `operators/` — `__init__` (core seeds
  and the registry), `elf` (`c_elf`), `cxx` (`cxx_more`), `platform`
  (ELF/loader/toolchain edits), `symbols`, `layout` (record and base layout
  classes), `vtable`, `enums`, `variables`, `aliases` (the edit-class probes of
  `model/probes.py`), `build_data` (compile modes only L3 build data records),
  `runtime_floor` (a raised libc version floor), `dependencies` (an import no
  library in the DT_NEEDED closure provides), `source_evidence` (what only
  L4 source replay or the L5 source graph sees; the gated `cxx_src` seed),
  `source_tree` (what only a multi-file, multi-TU tree shows; `cxx_tree`),
  `fragments` (union and bit-field edits on the gated `c_layout` seed, with
  exact rows from `model/layout_model.py`; campaign:
  `python -m abiforge.run.fragment_campaign OUT`; which width and barrier
  edits stand for the rest, under a named projection, is certified by
  `model/reduction.py`), `dialect` (identical C++ source rebuilt under a
  newer `-std=`; the gated `cxx_dialect` seed). The
  `EditOp` type is `model/opkit.py`. The ChangeKind scope table (which kinds
  are in scope, and why not the rest) is `coverage_scope.py`.
- **Seeds** (`model/seeds.py`): a seed's `build.*` roles carry compile and link
  inputs (version script, SONAME, flags) that an operator edits like a
  declaration; a seed with a `gate_role` (`c_elf_alt`, `cxx_alt`) takes only
  operators naming that role. `extra_files` adds further files to the tree: a
  header the public header includes but the dump is not given (a private
  header) or another translation unit compiled into the library.
- **Configs** (`model/config.py`): a `Config` is one library build (standard,
  producer, flags, evidence and, for `headers`, `stripped` (the `{B,H}` primary
  configuration: no DWARF, so no `.debug_*` section) and the header backend: castxml,
  clang or the native gcc plugin, which needs
  `python -m abicheck.extract.headers.gcc.build --compiler g++` once) plus its
  `consumers`. `build_data=True` adds L3 evidence on top of `headers`: the
  harness writes each side's `compile_commands.json` and the adapter dumps
  through the typed `--build-info` workflow. `sources=True` adds L4/L5 evidence
  the same way (`--sources`: each side's source tree, with its compile DB); its
  operators also need the clang header backend (`Config.header_backend`), the only backend whose source
  replay records macros, bodies and call edges. A `sources` tree is built
  under a `src/` project directory, so the two sides' paths compare across
  checkouts (abicheck anchors a path at its last project-layout segment).
  `follow_deps=True` (any evidence level) also resolves each side's DT_NEEDED
  closure (`--follow-deps`). A (build, consumer) mix that cannot work is an explicit
  `infeasible` pair, never a pass. Evidence is per pair by default; an
  operator with `new_evidence` (`model/opkit.py`) dumps its new side with less
  evidence (headers or DWARF absent), the asymmetric-evidence axis.
- **Run** (`run/`): `harness` builds and asks the checker, `exec` is the
  execution oracle (consumers, layout probes, new-header compiles), `cells`
  reconciles (false-clean first) and minimizes a failing cell into a
  ready-to-paste regression test, `tiers` defines PR/nightly/weekly, `report`
  summarizes pairs (`python -m abiforge.run.report RESULTS_DIR`).

## Adding to the space

Read `docs/contribute/testing.md` "Generated ABI edit corpus coverage" first.

- A new declared kind: `python -m abiforge.coverage ledger --write`.
- A new seed role or config axis value: its construct or config-feature entry
  in `model/space.py`.
- A new operator of a split construct (`edit_model.EDIT_MODEL`): its `OP_EDIT_CLASS`
  entry. An operator whose role is only a carrier (`elf.extra`, a class seed
  holding a free function) also needs its real construct in `OP_CONSTRUCT`. Edit
  classes come from `model/edit_model.py`, never from the operators; a class
  with no operator stays in the model as a reported gap.

## Comparing tools

`python -m abiforge.run.competitors OUT --select pilot` builds each selected
pair once per configuration (`dwarf+headers`, `stripped+headers`) and runs
the four modes on those files (`run/competitors.py`). Expected decisions and
details come from `run/competitor_truth.py` (operator rules, header diff,
compiler `sizeof`/`offsetof` probe), never from a tool; `run/competitor_score.py`
maps each tool's answer to a decision and detail outcomes;
`run/competitor_report.py` writes `pairs.csv`, `summary.json`, `table.md`,
`divergences.md`, `figure.svg`. Receipts: `receipts/competitors/`.

## Running

PR tier: `pytest tests/test_abi_edit_corpus.py -m integration`. Nightly:
`ABICHECK_EDIT_CORPUS_TIER=nightly pytest tests/test_abi_edit_corpus_matrix.py
-m edit_corpus_nightly` (`ABICHECK_EDIT_CORPUS_SEED`,
`ABICHECK_EDIT_CORPUS_SHARD=K/N`). Restrict toolchains with
`ABICHECK_EDIT_CORPUS_TOOLCHAIN` / `ABICHECK_EDIT_CORPUS_CONSUMER_TOOLCHAIN`
(or `--edit-corpus-toolchain` / `--edit-corpus-consumer-toolchain`); an
uninstalled name fails the run.
