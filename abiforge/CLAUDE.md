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

## Ground truth

- An expectation comes from the edit's ABI meaning, never from a checker's
  answer. An `oracle-conflict` cell means the expectation is wrong.
- A checker disagreement is fixed in the checker, or recorded in `known.py`
  as a strict xfail with its `docs/contribute/known-gaps.md` row.
- `requires(config)` reads only the config, never the host. A host
  capability (CPU, libstdc++, a flag the toolchain may refuse) is an `EditOp`
  field (`host_arch`, `needs_libstdcxx`, `probe_flags`) that
  `run/toolchains.capability_gap` turns into an *unavailable* cell.

## Where things live

- **Operators** and their ground truth: `operators/` — `__init__` (core seeds
  and the registry), `elf` (`c_elf`), `cxx` (`cxx_more`), `platform`
  (ELF/loader/toolchain edits), `symbols`, `layout` (record and base layout
  classes), `build_data` (compile modes only L3 build data records),
  `runtime_floor` (a raised libc version floor), `source_evidence` (what only
  L4 source replay or the L5 source graph sees; the gated `cxx_src` seed). The
  `EditOp` type is `model/opkit.py`.
- **Seeds** (`model/seeds.py`): a seed's `build.*` roles carry compile and link
  inputs (version script, SONAME, flags) that an operator edits like a
  declaration; a seed with a `gate_role` (`c_elf_alt`, `cxx_alt`) takes only
  operators naming that role.
- **Configs** (`model/config.py`): a `Config` is one library build (standard,
  producer, flags, evidence and, for `headers`, the header backend: castxml,
  clang or the native gcc plugin, which needs
  `python -m abicheck.extract.headers.gcc.build --compiler g++` once) plus its
  `consumers`. `build_data=True` adds L3 evidence on top of `headers`: the
  harness writes each side's `compile_commands.json` and the adapter dumps
  through the typed `--build-info` workflow. `sources=True` adds L4/L5 evidence
  the same way (`--sources`: each side's source tree, with its compile DB); its
  operators also need `header_backend="clang"`, the only backend whose source
  replay records macros, bodies and call edges. A (build, consumer) mix that cannot work is an explicit
  `infeasible` pair, never a pass.
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
- A new `struct`/`inheritance`/`function` operator: its `OP_EDIT_CLASS`
  entry. An operator whose role is only a carrier (`elf.extra`, a class seed
  holding a free function) also needs its real construct in `OP_CONSTRUCT`. Edit
  classes come from `model/edit_model.py`, never from the operators; a class
  with no operator stays in the model as a reported gap.

## Running

PR tier: `pytest tests/test_abi_edit_corpus.py -m integration`. Nightly:
`ABICHECK_EDIT_CORPUS_TIER=nightly pytest tests/test_abi_edit_corpus_matrix.py
-m edit_corpus_nightly` (`ABICHECK_EDIT_CORPUS_SEED`,
`ABICHECK_EDIT_CORPUS_SHARD=K/N`). Restrict toolchains with
`ABICHECK_EDIT_CORPUS_TOOLCHAIN` / `ABICHECK_EDIT_CORPUS_CONSUMER_TOOLCHAIN`
(or `--edit-corpus-toolchain` / `--edit-corpus-consumer-toolchain`); an
uninstalled name fails the run.
