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
   either fixed in the checker or recorded in `known.py`. Where an operator
   states exact rows (`model/exact.py`: kind, entity, member, old/new values
   with units, multiplicity, inside a declared scope), a right verdict and
   right kinds are not enough: a wrong member, value, missing, duplicated
   or unexpected in-scope row is a `wrong-detail` mismatch, and a row the
   configuration's evidence cannot establish is unknown (`evidence-limited`),
   never a pass.

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
second checker needs a translation of that vocabulary into its findings,
plus its adapter.

`checkers/abidiff.py` and `checkers/abicc.py` do that for libabigail and
ABICC at the level all tools share: a maintainer decision (break, source-only
break, compatible, unchanged) and explanation details (entity, fields, size,
offsets, affected functions). `python -m abiforge.run.competitors OUT
--select pilot` runs abicheck, abidiff, ABI Dumper→ABICC and ABICC on XML
descriptors on the same built pairs (`dwarf+headers` and
`stripped+headers`); receipts in `receipts/competitors/`.

## Union and bit-field fragments

`operators/fragments.py` generates the union and bit-field edits of the
compatibility-space research on the `c_layout` seed (`model/fragments.py`):
same-size union retypes, member add/remove at fixed layout, `char[N]`
extents around 8 and 16 bytes, bit-field widths 1/7/8/9/15/16/17/31/32,
`unsigned : 0` barriers inserted and removed before and after a field,
packing rebuilds (including alignment-only fixtures) and `-O2` controls.
Each operator's exact rows come per configuration from an independent
layout model (`model/layout_model.py`, SysV x86-64 gcc/clang, default
packing and `-fpack-struct=1`); `tests/test_abiforge_layout_model.py`
checks that model against compiler probes and raw DWARF. Other
configurations are not applicable, with the reason. The model is finite
evidence over this fragment, not a statement about all of C or C++.

```bash
python -m abiforge.run.fragment_campaign OUT   # receipt.json + cells.csv
```

## Boundaries

`tests/test_abiforge_boundaries.py` (in the abicheck repository) checks that
abiforge never imports `tests` or `pytest`, and that only `checkers/` imports
`abicheck`. That is what keeps it movable to its own repository.

## Research plan

The [compatibility-space reduction research plan](../docs/contribute/plans/abiforge-space-reduction-research.md)
collects prior work, proof obligations, experiments and acceptance criteria for
reducing validation cost beyond bounded enumeration. It distinguishes model
coverage from checker conformance and product-specific coverage.

### oneDAL SYCL consumer campaign (Linux)

The optional SDK campaign builds seven queue overloads and their host siblings
against oneDAL, withdraws the queue overloads from a generated bridge, runs
already-linked consumers, recompiles their source, and checks the exact grouped
removals through the abicheck CLI. It also compares the unchanged bridge. These
cells are independent of the plain C/C++ operator ledger.

```bash
micromamba create -y -p "$PWD/sdk" -c https://software.repos.intel.com/python/conda \
    dpcpp_impl_linux-64=2025.0.4 dal=2025.2 dal-include=2025.2
python -m abiforge.run.onedal_sycl results --prefix "$PWD/sdk"
```

The execution oracle requires a SYCL device with shared USM. The JSON receipt
retains loader and compiler outcomes separately from checker verdicts.

`python -m abiforge.run.sycl_plugins OUT` exercises PI and UR inventory
changes without an SDK. It builds real ELF plugins, resolves their entry points
in a fresh process, edits the installation in place, and compares stored dumps
with the snapshot cache enabled. The fixtures exercise the exported loader
interface only.

Run both integration tests with `ABIFORGE_ONEDAL_PREFIX="$PWD/sdk" pytest -m integration tests/test_abiforge_sycl_campaigns.py`.

### oneDAL-relevant L2 representatives on Linux x86-64

Run `python -m abiforge.run.l2_representatives OUT --frontend clang` or
select `castxml`. Repeat `--family layout`, `--family calls`, or
`--family virtual` to restrict the run. Use a separate output directory per
frontend. The generator writes C++ sources, one frozen executable per family,
stripped DSOs, public CLI snapshots and comparisons, and a checked JSON
receipt. Clang supplies the LLVM call signatures independently of either
header frontend. The regular corpus's `struct_swap_fields` and
`by_value_struct_became_nontrivial` operators supply the corresponding edits.

| Representative | Independent observation and fault distinguished |
| --- | --- |
| Equal-width field swap | A frozen reader returns 29 instead of 17. Both field offsets reverse and the record stays eight bytes. |
| Stronger alignment | The same eight-byte record begins at byte 8 instead of 4 inside an enclosing record. The client queries the provider's offset without passing it an under-aligned object. |
| Nontrivial destructor | A four-byte value moves from a register to an indirect argument and from a register return to a hidden result pointer. The checker must attribute exactly the two changed argument positions and the one affected return. |
| Register class at constant size | A 16-byte result changes from floating-point plus integer registers to two integer registers. Both versions still return directly. |
| Late argument with an existing hidden result | The same edit affects the third source parameter after an integer and a floating-point parameter. The compiler still emits a separate leading result pointer. |
| Pointer, reference and assignment controls | Frozen pointer/reference calls keep returning 34 in total. User-provided copy assignment alone leaves all five LLVM call signatures stable. Other properties such as trivial copyability can still change. |
| Insert and reorder virtual slots | The two frozen calls through the secondary base return 19/17 after insertion and 18/17 after a swap. Checking only vtable growth would miss the swap. |
| Override in the primary or secondary base | Each frozen call reads the derived object's payload. The primary override reuses its slot. The secondary override also adds a primary entry, growing this complete vtable from 64 to 72 bytes. |
| Reorder polymorphic bases | The old client's baked-in base adjustment reaches the other subobject and reads 41 instead of 17. Both base offsets must change in the checker report. |
| Implementation-only controls | One per family checks the frozen workload and requires an empty ABI diff. |

These representatives cover the specified SysV AMD64 C++ decisions. ELF
withdrawals, actual SYCL queue dependencies, and two-rank collectives have
separate [oneDAL product witnesses](../research/onedal-coverage/product/README.md).

The recorded Linux runs contain 15 comparisons per frontend, plus the five
baselines. [Clang receipt](../research/onedal-coverage/product/l2-representatives-clang.json)
and [CastXML receipt](../research/onedal-coverage/product/l2-representatives-castxml.json).

For the release verdict gate run
`python -m abiforge.run.l2_representatives OUT --frontend castxml --verdict-only`
(or select `clang`). This checks each generated binary pair with the compiler
or frozen-client witness, then requires the public verdict and CLI exit code
to agree. A risk warning fails the gate for a known binary incompatibility.
Omit `--verdict-only` to check diagnostic attribution too.

Two additional pairs exercise an exported callback registration signature.
Removing Clang's `trivial_abi` attribute from a nontrivial record changes
how an old callback receives the record by value. On SysV AMD64 the frozen
client's exit changes from 0 to 1. Applying the same edit to the reference
case preserves the client's result. The gate now checks 15 comparisons
per frontend.
