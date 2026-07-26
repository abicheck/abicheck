# PVXS full-version-matrix scan through latest `master` (2026-07-26)

Follow-up to `pvxs-abi-validation-2026-07.md` (F1–F7) and
`pvxs-abicheck-acceptance-2026-07-18.md` (FP-1–FP-3). Those two passes
covered individual tag pairs and one hand-picked historical diff; this run
does the **full pairwise version matrix** — every consecutive release plus
the upstream repository's current tip — built from a real, from-scratch
toolchain (the acceptance spike's "still owed" step) rather than resumed
from a pre-existing checkout. It also verifies the abicheck **GitHub Action**
end-to-end against real pvxs binaries, closing the "recommended CI
integration" section of the first report from a paper design into a
verified one.

pvxs's default branch is named **`master`**, not `main` — "latest main" below
means its tip, `cc7bc72dd7676c72871889c8586014947567ed1d` (2026-07-09), which
is also the exact revision the acceptance spike named as its "new" side and
is unchanged 17 days later (2026-07-26), i.e. still genuinely current.

## Reproduction

Toolchain: gcc/g++ 13.3.0, clang 18.1.3 (**no castxml** on this host — every
header-AST parse below uses `--ast-frontend clang`), Python 3.11.15, 4 cores.
`compiledb` (pip) substitutes for `bear` (not installed) to generate a compile
database for the source-depth attempt.

```sh
apt-get install -y libevent-dev   # pvxs's UDP/TCP event loop dependency;
                                   # not present by default on this image —
                                   # its absence is a hard build failure
                                   # (#error libevent not built with threading
                                   # support), not an abicheck concern.

git clone --branch R7.0.10 --depth 1 https://github.com/epics-base/epics-base
make -C epics-base -j4                          # R7.0.10 — current latest tag
                                                 # (previous runs used R7.0.8.1)

git clone https://github.com/epics-base/pvxs
cd pvxs && git fetch --tags
for ref in 1.4.0 1.5.0 1.5.1 1.5.2 master; do
  git worktree add --detach "../build-src/$ref" "$ref"
  echo "EPICS_BASE=$PWD/../epics-base" > "../build-src/$ref/configure/RELEASE.local"
  make -C "../build-src/$ref" CROSS_COMPILER_TARGET_ARCHS= \
    OPT_CFLAGS='-g -Og' OPT_CXXFLAGS='-g -Og' ioc -j4
done
```

All five builds succeeded cleanly (`rc=0`) once `libevent-dev` was installed,
each yielding `libpvxs.so.<abi>` and `libpvxsIoc.so.<abi>` with DWARF.

## Version matrix (L1+L2, public-header-scoped, `--ast-frontend clang`)

| Pair | libpvxs | libpvxsIoc | Notes |
|------|---------|------------|-------|
| 1.4.0 → 1.5.0 | **API_BREAK** (11) | **BREAKING** (2 breaking / 14) | SONAME `libpvxs.so.1.4`→`.1.5`; real minor-release break |
| 1.5.0 → 1.5.1 | COMPATIBLE_WITH_RISK (6) | COMPATIBLE_WITH_RISK (4) | Patch |
| 1.5.1 → 1.5.2 | **BREAKING** (1 breaking / 10) | **BREAKING** (9 breaking / 29) | Patch — see "what the findings are" below |
| 1.5.2 → master | **NOT_COMPARABLE** (both) | **NOT_COMPARABLE** (both) | New public header added upstream — see F8 |

`1.5.1 → 1.5.2`'s single `libpvxs` break is `func_removed_elf_only` (an
exported symbol genuinely absent from the new binary — real, not the
`OperationBase` internal-header false positive the first report's F3
documented; that one is a *field addition*, a different mechanism, and does
not appear here because public-header scoping was active throughout this
run). `libpvxsIoc`'s larger break/risk count is dominated by
`symbol_leaked_from_dependency_changed` (13) — libstdc++/libgcc symbols that
leaked into the public surface changing shape between builds, a library
*quality* signal (fix: `-fvisibility=hidden`), not a real consumer-facing
break — and `func_removed_elf_only` (9), genuinely removed exported symbols.
Spot-checked several of the surviving `exported_object_alignment_reduced`
findings on these three pairs (`cnt_PutOperationCache`, `linkGlobal`,
`pvar_dset_devLoPDBQ2UTag`, …) — all are real namespace-scoped or EPICS
device-support globals, not mangling artifacts, so they correctly still
fire; the new exemption below (F9) does not touch them.

## F8 (fixed, follow-up pass): a new public header trips the scope-comparability gate

**Symptom.** `compare 1.5.2-build/libpvxs.so master-build/libpvxs.so -H
1.5.2-build/include -H master-build/include ...` exits `16` with no verdict:

```text
Error: 'libpvxs.so.1.5' old='1.5.2' new='master' are not comparable: old and
new snapshots do not cover the same declared surface (scope_fingerprint
mismatch) — the comparison is not comparable. This commonly means a
manifest/CLI-flag drift between the two extraction runs, not a real API
change.
```

**Root cause.** Master added exactly one new file, `include/pvxs/json.h`
(diffed the two `include/` trees directly to confirm — no other pair in the
matrix above adds or removes a header). `abicheck/comparability.py`'s
`scope_fingerprint` (ADR-050 D2) hashes the full *set* of declared public
header files per side; a directory-based `-H old=<dir> -H new=<dir>` input
expands to each side's actual header file list, so old (11 files) and new
(12 files) fingerprint differently and the D2 gate — designed to catch
"manifest/CLI-flag drift between two extraction runs" — hard-fails instead
of producing a diff.

**Why this is real friction, not a corner case.** A released library gaining
a new public header between minor releases (a new feature getting its own
header) is ordinary, common evolution — exactly the kind of change a
"полноценный" (full/comprehensive) scan through a project's live tip needs to
handle, not something to special-case around. Every *other* tag-to-tag pair
in the pvxs history checked above kept an identical header set and never hit
this, which is why neither of the two earlier validation passes (both capped
at 1.5.2 or a single hand-picked historical diff) surfaced it — it only shows
up once a scan genuinely reaches current `master`.

**Original workaround, before this branch's carve-out fixes (ADR-050's
sanctioned escape hatch).** `--diagnostic-comparison` forced the diff
through, stamping `assurance: "none"` instead of silently upgrading
confidence:

```sh
abicheck compare 1.5.2-build/libpvxs.so master-build/libpvxs.so \
  -H old=1.5.2-build/include -H new=master-build/include \
  --include old=<epics>/include ... --include new=<epics>/include ... \
  --ast-frontend clang --diagnostic-comparison
# → COMPATIBLE_WITH_RISK, 10 risk + 1 addition (after F9 below), assurance=none
```

The forced diff is sane: 7 `imported_symbol_added` findings for the new
`yajl_*` C API calls `json.h` makes directly, `runpath_changed` (expected —
each side built in its own separate tree), 2×`header_binary_context_mismatch`,
and a `declaration_renamed` for an unrelated unnamed-enum reconciliation. No
spurious breaking findings.

**Superseded by the fix below (Codex review, PR #641 follow-up):
`--diagnostic-comparison` is no longer needed for this exact scenario.**
Once the additive-only scope/header-sequence/include-sequence carve-outs
below all landed, the same command **without** the flag succeeds directly
— `1.5.2` and `master` both ran a real L2 header-AST frontend, so this is
the ordinary case those carve-outs target, not one of the cases they still
decline (see "Known, accepted limitation" below). Verified via a direct
repro of the exact scope/profile-field shape this pair produces (both
`scope_fingerprint` and `profile_fingerprint` genuinely differ, and
`check_contracts_comparable` returns `None` for neither error). The escape
hatch remains necessary only for cases the carve-outs correctly decline
(a header landing outside the old side's common ancestor directory, or
genuinely unrelated, uncorroborated profile drift) — reaching for it for
this case now is unnecessary and bypasses comparability validation the
gate no longer needs waived here.

**Fixed in a follow-up pass, with an ADR record, not a drive-by patch.** The
gate cannot locally distinguish "upstream added a real header" from "the
caller's CLI/manifest drifted between two extraction runs" — both produce the
identical symptom (a different declared header set) — but it *can* tell
**direction**: a genuine drift can shrink, grow, or replace the declared set
in an unprincipled way, while ordinary library evolution overwhelmingly shows
up as the new side's declared surface being a strict superset of the old
side's. `abicheck/comparability.py` now checks each `SCOPE_FIELD_KEYS` field
(`headers`, `public_header_dirs`) independently for a superset relationship
(`_scope_field_is_additive_superset`) before hard-failing — a pure addition
in every differing field skips the gate and lets the ordinary diff engine
report the new declarations as additions, while a removal, rename, or
disjoint set (even alongside a co-occurring addition) still hard-fails
exactly as before. A side that collapses to the existing
`<single-header>`/`<single-header-dir>` sentinel (no real per-entry identity
to verify a superset against) still declines the carve-out and hard-fails,
same as it always did. Documented as its own ADR-050 D2 subsection
("Additive-only header-set carve-out"), with new regression tests in
`tests/test_comparability.py` pinning: a pure addition (allowed), a removal
alongside an addition (still raises), a pure removal (still raises), a
single-header-sentinel side (still raises, carve-out declines), independent
per-field growth on `public_header_dirs`, one field growing while the other
shrinks (still raises overall), and `--diagnostic-comparison` interaction.
`--diagnostic-comparison` remains available unchanged for the cases this
carve-out still correctly declines.

## F9 (fixed): a second RTTI-shaped alignment false positive, found live

Forcing the `1.5.2 → master` `libpvxs` diff (above) surfaced one more
finding before the fix below:

```text
exported_object_alignment_reduced | _ZZNKSt7__cxx1112regex_traitsIcE16lookup_classnameIPKcEENS1_10_RegexMaskET_S6_bE12__classnames
  Exported object alignment reduced: ... (512 → 128 bytes)
```

This is the exact same underlying bug class the first report's F2 fixed
(`_check_object_alignment_reduced`'s address-derived alignment heuristic
producing noise for a symbol no header can ever declare) but a **different
mangling shape**: not an RTTI prefix (`_ZTV`/`_ZTI`/`_ZTS`/`_ZTT`), but the
generic Itanium `<local-name>` production (`_ZZ...`) — here, a libstdc++
`<regex>` template instantiation's function-local `static` lookup table. F2's
existing exemption only covers the four RTTI prefixes, so it didn't catch
this.

**Fix** (`abicheck/name_classification.py`, `abicheck/diff_platform_elf_symbols.py`):
added `LOCAL_NAME_PREFIX`/`is_local_name_symbol()` (any `_ZZ`-prefixed
symbol — a local-name-production entity is by construction never named by a
header declaration) and wired it into `_check_object_alignment_reduced`
alongside the existing RTTI check. Deliberately **not** extended to
`_check_symbol_size_change`: unlike address-derived alignment (an inferred
heuristic), `st_size` is a direct, real symbol-table fact regardless of a
symbol's scope, so a local-name symbol's size change stays meaningful signal
there.

**Refined after PR review** (`chatgpt-codex-connector`, P2): the initial fix
exempted *every* `_ZZ`-prefixed symbol, which is too broad — a **public**
inline/template function belonging to the library under test (not the C++
runtime) can itself own a function-local `static` that Itanium's
`STB_GNU_UNIQUE`/weak-symbol mechanism cross-TU-deduplicates, so consumers
genuinely can bind against it and rely on its declared alignment; a real
regression there must still fire. Narrowed to
`is_stdlib_local_name_symbol()` — a regex matching the local-name production
only when the *enclosing function* is itself `std::`/`__gnu_cxx::`/
`__cxxabiv1::` (mirroring `STDLIB_RTTI_PREFIXES`'s existing, reviewed
toolchain-vs-library-under-test distinction for the RTTI case), so a
library's own public inline-function statics are no longer swept into the
exemption. Confirmed with a new test using a synthetic
`pvxs::`-namespaced local-name symbol that must still fire.

Verified against the real pair: before the fix, `libpvxs 1.5.2 → master`
(diagnostic-forced) reported 11 risk + 1 addition (12 total); after, 10 risk
+ 1 addition (11 total) — the exact spurious finding gone, verdict unchanged
(`COMPATIBLE_WITH_RISK`). New regression tests: `tests/test_name_classification.py`
(`test_is_local_name_symbol_true/false`, `test_is_stdlib_local_name_symbol_true/false`,
including a restrict-qualified (`r`) stdlib match and a truncated-namespace
rejection case caught in a later review pass — see below),
`tests/test_coverage_extension_elf.py::TestObjectAlignmentReduced::test_stdlib_local_name_symbol_is_exempt`
(uses the exact real mangled name from this run) and
`test_library_owned_local_name_symbol_still_fires` (the Codex-flagged case).
Full fast unit suite (19543 passed at this point / 30 skipped / 4 xfailed,
`--cov-branch` 96–98% line/branch coverage on both touched modules), `mypy
abicheck/`, and `ruff check abicheck/ tests/` all clean at this point in the
review (see more rounds of fixes below — 19558 passed is the true final
count).

**Further refined after two more PR review passes** (`chatgpt-codex-connector`,
`coderabbitai`): the qualifier character class was missing the `r` (restrict)
CV-qualifier entirely, and `10__cxxabiv1?`'s optional trailing digit could
over-match a truncated, never-actually-emitted `10__cxxabiv` + arbitrary next
character instead of requiring the complete, exact `__cxxabiv1` namespace
name. `_STDLIB_LOCAL_NAME_RE` now uses `[rVK]{0,3}[RO]?` (CV-qualifiers and
ref-qualifier as separate, correctly-bounded groups) and the full
`10__cxxabiv1` literal, with regression tests pinning both the
previously-unmatched restrict-qualified case and the previously-over-matched
truncated one.

**A fourth review pass** (`chatgpt-codex-connector`, `coderabbitai`) found two
more gaps in the same narrowed exemption:

- The exemption fired unconditionally, even when the snapshot **under
  comparison is itself the C++ runtime** (`libstdc++`/`libc++`/`libc++abi`)
  — there a `std::`/`__cxxabiv1::` local static *is* the library-under-test's
  own ABI surface, not leaked toolchain noise, exactly the distinction
  `model.stdlib_namespaces_excluded`/`dumper_elf_symbols._elf_classify_symbols`
  already draw elsewhere for other detectors. Fixed by gating
  `is_stdlib_local_name_symbol(sym_name)` on
  `stdlib_namespaces_excluded(old, new)` in `_check_object_alignment_reduced`
  — the exemption now only applies when *neither* side is identified as the
  C++ runtime itself.
- `_STDLIB_LOCAL_NAME_RE` missed the Itanium ABI's **standard-substitution**
  codes for extremely common `std::` templates (`Sa`=`std::allocator`,
  `Sb`=`std::basic_string`, `Ss`=`std::string`, `Si`/`So`/`Sd`=`std::istream`/
  `ostream`/`iostream`) — a local static inside e.g.
  `std::allocator<int>::f() const` mangles via `Sa`, occupying the exact
  grammar slot the bare `St` substitution does, so it was silently missed.
  Added to the alternation.

Both confirmed and fixed in the same PR, with new regression tests: a
`library="libstdc++.so.6"` snapshot pair proving the alignment finding still
fires when the runtime is the thing under test, and one parametrized case
per standard-substitution code. Full fast unit suite (19556 passed / 30
skipped / 4 xfailed), mypy/ruff clean after this revision too.

**A fifth review pass** (`chatgpt-codex-connector`) found one more gap in the
same regex and one unrelated issue in this section's recommended workflow
YAML:

- `_STDLIB_LOCAL_NAME_RE` still missed libstdc++ debug mode
  (`_GLIBCXX_DEBUG`)'s `__gnu_debug::` namespace (mangled as `11__gnu_debug`)
  — already a recognized stdlib/runtime implementation namespace elsewhere
  in `name_classification.py` (`_STDLIB_TYPE_NAMESPACE_PREFIXES`), so a local
  static in one of its functions still produced the false positive. Added.
- The recommended workflow above grants `security-events: write` but
  referenced `actions/checkout@v4` and `github/codeql-action/upload-sarif@v3`
  by mutable tag — this repo's own convention (`AGENTS.md`) is to pin every
  action running with an elevated permission to a full commit SHA, so a
  repointed/compromised tag can't run with that token. Fixed by reusing this
  repo's own already-audited pins for both (the same ones `action.yml` and
  `.github/workflows/ci.yml`/`security.yml` use).

Fixed with one more regression test (`test_gnu_debug_local_name_is_exempt`)
and the two `uses:` lines below now SHA-pinned. Full fast unit suite (19558
passed / 30 skipped / 4 xfailed), mypy/ruff clean at this point.

**A sixth review pass** (`chatgpt-codex-connector`) found that
`_STDLIB_LOCAL_NAME_RE` still didn't handle *recursively nested*
`<local-name>` productions: a lambda or local class defined inside a stdlib
function is itself "local" to that function, so a `static` local to the
lambda's own call operator mangles with one extra leading `Z` per nesting
level before the qualifiers/namespace — e.g. real GCC output for
`std::outer()::{lambda()#1}::operator()() const::x` is
`_ZZZSt5outervENKUlvE_clEvE1x` (three `Z`s, not the one `_ZZ...` handled so
far). Fixed by allowing any number of extra leading `Z`s
(`^_ZZZ*N?[rVK]{0,3}[RO]?...`) before the qualifier/namespace check — safe
because none of the recognized namespace markers themselves start with `Z`,
so a chain that bottoms out at a non-stdlib function (a lambda inside the
library-under-test's own code) still correctly fails to match. New
regression tests: `test_nested_stdlib_lambda_local_name_is_exempt` (the
exact GCC example above) and `test_nested_library_owned_lambda_local_name_still_fires`
(the same nesting shape, but rooted in a `pvxs::` function — must still
fire). Full fast unit suite (19562 passed / 30 skipped / 4 xfailed),
mypy/ruff clean.

**A seventh review pass** (`chatgpt-codex-connector`) caught an
inconsistency in the fifth pass's action-pinning fix: `actions/checkout` and
`github/codeql-action/upload-sarif` were pinned, but `abicheck/abicheck@v0.4.0`
in the same `security-events: write` job was left as a mutable tag — the
identical risk applies regardless of which repository authors the action.
Fixed by pinning it to the commit `v0.4.0` resolves to
(`7bbc3ca44d7548bb52c73ef6af6b2476ce51549b`), with the same
`# v0.4.0` audit comment convention as the other two — this is the final
state of the fix.

## CI / GitHub Action integration — verified end-to-end

The first report's "Recommended CI integration for pvxs" section was a
hand-drafted `abi.yml` design, never run. This pass instead exercises the
**real** composite Action scripts (`action/run.sh`, the same file
`action.yml`'s `runs.steps` invokes) directly against the real pvxs 1.5.1 →
1.5.2 `libpvxs.so` pair, simulating exactly what a GitHub Actions runner does
(the same `INPUT_*`/`GITHUB_OUTPUT`/`GITHUB_STEP_SUMMARY` environment-variable
contract `action.yml` sets up):

```sh
INPUT_MODE=compare \
INPUT_OLD_LIBRARY=.../1.5.1/libpvxs.so.1.5 INPUT_NEW_LIBRARY=.../1.5.2/libpvxs.so.1.5 \
INPUT_OLD_HEADER=.../1.5.1/include INPUT_NEW_HEADER=.../1.5.2/include \
INPUT_INCLUDE="<epics-base include dirs, one per line>" \
INPUT_AST_FRONTEND=clang INPUT_FORMAT=json INPUT_FAIL_ON_BREAKING=true \
INPUT_ADD_JOB_SUMMARY=true \
GITHUB_OUTPUT=... GITHUB_STEP_SUMMARY=... \
bash action/run.sh
```

Result: `run.sh` assembled the exact `abicheck compare` invocation, ran it,
and set `verdict=BREAKING`, `exit-code=4`, `report-path=...` in
`$GITHUB_OUTPUT` — matching the direct-CLI verdict for the same pair — plus a
correctly-rendered markdown Job Summary. This confirms the Action layer
(input assembly, side-aware `--header`/`--include` flag construction, exit
code mapping, job summary) works correctly against a real, non-trivial C++
library, not just the synthetic fixtures `tests/fixtures/action/` and
`.github/workflows/test-action.yml` already cover.

**Recommended pvxs integration**, updated from the first report to reflect
what this pass actually validated (`dependency-source: conda-forge`, this
repo's current default, installs castxml + a matching gcc/g++ via pixi — a
castxml-having runner was not available to test directly here, but
`ast-frontend: clang` is confirmed as the correct fallback for a
clang-only/no-castxml runner, which this whole validation pass ran under):

Three corrections from an initial draft of this section, all caught in PR
review (`chatgpt-codex-connector`) and confirmed before fixing: a bare
`header:` applies that *one* tree to **both** operands (`old-header:`/
`new-header:` are the side-scoped inputs — required here since old and new
genuinely have different header sets, the F8 scenario above), `format: sarif`
is rejected before comparison runs for a directory/package operand
(`old-library`/`new-library` pointing at a directory fans out over both
SONAME-matched libraries, but SARIF/`upload-sarif` is single-pair-only), and
— since this job grants `security-events: write` — every third-party action
running in it must be pinned to a full commit SHA (a repointed/compromised
mutable tag would otherwise run with that elevated token and could tamper
with code-scanning results), per this repo's own convention
(`AGENTS.md`, and `action.yml`'s own `actions/setup-python`/
`github/codeql-action/upload-sarif` pins). The corrected form below instead
does one single-pair `compare` per library — the same operand shape this
report's "CI / GitHub Action integration" section above actually ran and
verified, not a new untested shape — and reuses this repo's own
already-audited pins for `actions/checkout` and
`github/codeql-action/upload-sarif`.

**One more correction, from an eighth review pass** (`chatgpt-codex-connector`):
the `abicheck/abicheck` pin itself was wrong on release grounds, not just
security grounds. Checked directly against this repo's own history: the
ADR-050 D2 comparability gate and `--diagnostic-comparison` flag this whole
F8 section is built around landed in `c4f34df` (2026-07-25) — **after**
*both* `v0.4.0` (2026-07-01) and the current latest release `v0.5.0`
(2026-07-16). Pinning to either tag's commit would have meant the pinned
Action predates the feature entirely: without `extra-args:
'--diagnostic-comparison'` it wouldn't fail closed on the F8 mismatch the
way this section describes (that behavior didn't exist yet), and adding the
flag would fail with an unknown-option error (the flag didn't exist yet
either) — exactly the opposite of what the surrounding prose promises.
Since no released tag contains the feature yet, the pin below instead
targets `main`'s current tip (`c9e135a`, this PR's own base commit, which
does contain `c4f34df` and the flag) — genuinely illustrative of the intended
integration today, not something to copy-paste as a permanent pin: once a
release ships the feature (and once this PR's F9 fix merges), retarget to
that release's tag/commit instead.

**A ninth review pass** (`chatgpt-codex-connector`) found one gap in each of
the last two fixes:

- The `c9e135a` pin above is this PR's own *base* commit — it predates this
  PR entirely, so it contains the D2 gate/flag but **not** F9 (the local-name
  alignment fix this whole report is about). A user following this
  recommendation today would run a workflow that never exercises the fix.
  Since this PR hasn't merged yet, there is no mainline commit that has both
  the gate and F9 simultaneously other than this PR's own branch tip. The
  pin below now targets `612e481f` (this PR's HEAD as of the commit fixing
  this finding, PR #641), with the same "retarget once released" caveat as
  before, made more explicit: this is a temporary, PR-scoped reference, not
  a stable target.
- `is_stdlib_local_name_symbol()` classified **any** `std::`-nested local
  name as stdlib-owned, including a **user's own specialization** of a
  standard customization-point template (e.g. `template<> struct
  std::hash<MyType> { ... }` is user-authored code, unlike an ordinary
  instantiation like `std::vector<MyType>`, which is 100% stdlib-authored
  code merely instantiated for `MyType`). Real GCC output for an inline
  `std::hash<MyType>::operator()`'s local static
  (`_ZZNKSt4hashI6MyTypeEclERKS0_E4salt`) matched the stdlib-owned check and
  was wrongly exempted. Fixed with a new, deliberately non-exhaustive
  `_USER_SPECIALIZABLE_STD_TEMPLATE_RE` covering the standard's most commonly
  specialized customization points (`hash`, `less`, `greater`, `equal_to`,
  `not_equal_to`, `less_equal`, `greater_equal`, `char_traits`,
  `numeric_limits`, `iterator_traits`, `default_delete`, `formatter`) — a
  match on any of these **always** excludes the symbol from stdlib-owned
  classification, even for a stdlib-provided instantiation like
  `std::hash<int>`, erring toward reporting a possibly-noisy finding rather
  than risking hiding a real one (this can never be a complete list: the
  standard permits specializing effectively any library template this way,
  a limitation string-based mangled-name classification can't fully close
  without a real demangler or type-graph analysis).

New regression tests: `test_is_stdlib_local_name_symbol_user_specialized_customization_point_false`
(`hash`/`less`/`greater` cases) and
`test_user_specialized_std_hash_local_name_still_fires` (the exact GCC
example, end-to-end through the detector). Full fast unit suite (19567
passed / 29 skipped / 4 xfailed), mypy/ruff clean — this is the final state
of the fix.

**A tenth review pass** (`chatgpt-codex-connector`), on the F8 additive-only
header-set carve-out and the customization-point allowlist added in this
same follow-up branch, found three more gaps:

- `check_contracts_comparable`'s F8 carve-out returned `None` as soon as
  the scope mismatch was waived — an early return from the whole function,
  which also silently skipped the `profile_fingerprint` check immediately
  below it. A release that both adds a header (the additive, safe case)
  **and** changes an unrelated, uncorroborated extraction-profile field
  (compiler flags, macros, include order — not covered by any existing
  profile carve-out) would have been wrongly waved through as fully
  comparable instead of raising `ProfileMismatchError` for the second,
  genuinely unsafe drift. Confirmed and fixed: the carve-out is now gated
  into the scope condition's own boolean expression rather than an early
  `return None` inside the block, so waiving the scope mismatch falls
  through to the profile check that follows instead of bypassing it.
  Verified against a real repro (stashed the fix, confirmed the new test
  fails with "DID NOT RAISE ProfileMismatchError," then restored it).
- The `_USER_SPECIALIZABLE_STD_TEMPLATE_RE` allowlist added for the
  `std::hash<MyType>` case was still missing `std::swap` — a *function*
  template (not a class template like the others already listed) the
  standard equally explicitly permits specializing for a program-defined
  type (`template<> inline void std::swap<MyType>(...)`, mangling as
  `_ZZSt4swapI6MyTypeE...`). Added `4swap` to the alternation.
- This report's own recommended workflow pin (`612e481f`) predates the
  commit that introduces `_scope_field_is_additive_superset` (F8) — a user
  following this recommendation today for the exact `1.5.2 → master`
  scenario the report describes would still hit the F8 scope mismatch
  instead of the promised SARIF result. Retargeted below to `d0d34097`
  (this same follow-up branch's HEAD as of the commit fixing this finding),
  with the same "temporary, PR-scoped, retarget once released" caveat as
  every prior round of this same correction.

New regression test: `test_gate_additive_header_set_carve_out_still_checks_profile_afterward`
(the exact "additive scope + unrelated profile drift" scenario, proven to
fail without the fix and pass with it) and a new `4swap` parametrize case
in `test_is_stdlib_local_name_symbol_user_specialized_customization_point_false`.
Full fast unit suite green, mypy/ruff clean.

**An eleventh review pass** (`chatgpt-codex-connector`), on the same two
fixes, found one more gap in each:

- The customization-point allowlist was still missing `std::tuple_size` —
  another standard class-template customization point (used to support
  structured bindings) a program can legally specialize for its own type,
  the exact same "user-authored code nominally in namespace std" shape as
  `std::hash`/`std::swap`. Real GCC output for such a specialization's
  local static: `_ZZNSt10tuple_sizeI6MyTypeE1fEvE1x`. Added `10tuple_size`
  to the alternation, with a parametrized regression case.
- **The tenth pass's own profile-check fix, verified in isolation, exposed
  a second gap the moment it was checked against the actual real-world F8
  scenario end-to-end: `profile_fields["header_sequence"]` (declared-header
  *order*, tracked separately from scope's order-independent declared set —
  see `compute_extraction_contract`) necessarily changes on the exact same
  "pure addition" case, so `check_contracts_comparable` still raised
  `ProfileMismatchError` immediately after correctly falling through the
  now-fixed scope carve-out.** Confirmed by direct repro before writing any
  fix: `declared_headers=[a,b]` → `[a,b,c]` (everything else held constant)
  differed on `header_sequence` alone and still hard-failed — meaning F8
  was *still* not actually fixed end-to-end for any snapshot pair that also
  carries a `profile_fingerprint` (i.e. ran the L2 frontend — the ordinary
  case for a real header-AST dump, not an edge case). Fixed with a second,
  symmetric carve-out: a `profile_fingerprint` mismatch confined to
  `header_sequence` alone does not raise when the new sequence, with
  exactly the newly-added headers removed (preserving order), reconstructs
  the old sequence exactly (`_header_sequence_is_additive_reorder_free`) —
  proving no *existing* header was reordered relative to another, only new
  ones appended/inserted. A reorder of existing headers entangled with
  growth (`[a,b]` → `[b,a,c]`) still raises, since header order can
  genuinely change how an earlier-declared header's macros/pragmas
  resolve. Re-verified the real pvxs-shaped repro end-to-end with both
  carve-outs together: `check_contracts_comparable` now returns `None` —
  no exception of either kind — for the actual scenario this whole F8
  section describes.

New regression tests: a `10tuple_size` parametrize case, plus seven direct
unit tests of `_header_sequence_is_additive_reorder_free` (pure append,
mid-sequence insertion, pure reorder — declined, reorder entangled with
growth — declined, pure removal — declined, single-header-sentinel on
either side — declined, `None` inputs — declined) and one gate-level
end-to-end test reproducing the full real scenario (both fingerprints
differ, `check_contracts_comparable` returns `None`). Full fast unit suite
green, mypy/ruff clean, 97% branch coverage on `comparability.py`.

**A twelfth review pass** (`chatgpt-codex-connector`) found one more gap in
the F8 carve-out itself, plus one unrelated pinning gap this section's
own newly-added warning had just introduced:

- **The scope carve-out's `all(...)` checks every `SCOPE_FIELD_KEYS`
  field, not only the ones that actually differ — unlike the profile side,
  which pre-filters to a `differing` set before ever calling a carve-out.**
  The real F8 CLI shape is `-H old=<dir> -H new=<dir>` — a *single*
  `public_header_dir` per side — so `public_header_dirs` collapses to the
  identical `"<single-header-dir>"` sentinel on *both* sides even though
  old and new point at different physical directories. Confirmed by direct
  repro before any fix: exactly this shape (`declared_headers=[a,b]` →
  `[a,b,c]`, `public_header_dirs=[old_dir]` → `[new_dir]`, otherwise
  matching the real invocation) still raised `ScopeMismatchError`, because
  `_scope_field_is_additive_superset` declined on the sentinel
  *unconditionally*, before ever checking whether the two sides were
  actually equal — wrongly hard-failing on a field that never changed at
  all, before the carve-out could reach the genuinely differing `headers`
  field. Fixed: the helper now returns `True` immediately when
  `old_value == new_value`, regardless of shape. Re-verified the real
  directory-based F8 repro end-to-end afterward: `check_contracts_comparable`
  now returns `None`.
- This section's own SARIF/Code-Scanning `docs/use/output-formats.md`
  example had just gained an explicit `security-events: write` permission
  and a "pin every `uses:` here" warning in an earlier round, but its
  pre-existing `conda-incubator/setup-miniconda@v3` step was left on a
  mutable tag — now running in the same elevated job the warning describes,
  contradicting it. Resolved the real commit `v3`/`v3.3.0` resolves to
  (`fc2d68f6413eb2d87b895e92f8584b5b94a10167`, confirmed via
  `git ls-remote --tags`, a lightweight, non-annotated tag so the resolved
  SHA is the commit itself) and pinned it the same way as the other two
  actions in that job.

New regression tests: `test_gate_additive_header_set_carve_out_ignores_unchanged_single_dir_sentinel`
(the exact real directory-based F8 shape, proven to fail without the fix)
and `test_scope_field_additive_superset_true_for_unchanged_single_entry_sentinel`
(the pure-function-level pin). Full fast unit suite green, mypy/ruff clean.

**A thirteenth review pass** (`chatgpt-codex-connector`) found the header-
sequence fix above still didn't reach the real *production* invocation
shape, plus a second, more general structural gap in how carve-outs
compose:

- The production `dump` path (`cli_dump_helpers.py`) calls
  `resolve_inferred_header_roots`, auto-adding the header-owning directory
  as a declared include — so the real `-H old=<dir> -H new=<dir>`
  invocation changes `profile_fields["include_sequence"]` too, not just
  `header_sequence`. Confirmed by direct repro calling
  `resolve_inferred_header_roots` itself (not a hand-built contract that
  skips it, to match the exact production code path):
  `differing = {"header_sequence", "include_sequence"}`, and the
  header-sequence carve-out alone declined because `include_sequence` was
  also present — `check_contracts_comparable` still raised
  `ProfileMismatchError` for the real F8 CLI shape even after the previous
  round's fix. Added a fourth carve-out
  (`_include_sequence_is_additive_owned_growth`): a mismatch confined to
  `include_sequence` doesn't raise when every differing slot's owned
  `"hdrs:..."` token is itself a pure superset growth; a slot-count change,
  an `"ext:"`/`"label:"` slot differing, or a `<single-header>` sentinel
  still raise.
- **A more general gap: carve-outs didn't compose.** Each required
  `differing` to match its own static field-set *in full* — so a release
  combining two independently-sanctioned deltas (adding a header **and**
  making a corroborated C++-standard raise) produced
  `differing = {"header_sequence", "language_standard"}`, matching neither
  carve-out alone, and still raised even though each half was individually
  fine. Confirmed by direct repro before any fix. Restructured the profile
  check into one composing loop: each carve-out claims and verifies only
  the subset of `differing` it understands, narrowing a shared
  `unexplained` set; the pair is comparable once nothing remains
  unexplained. The four carve-outs' field-sets are mutually disjoint, so
  this changes no single carve-out's own safety invariants — only which
  *combinations* of independently-safe deltas are now recognized together.
- **One further gap found during this investigation, deliberately
  documented as a known limitation, not fixed:** a header added *outside*
  the old side's common ancestor directory shifts the common root every
  remaining `headers` identity is computed relative to, so even the
  existing headers' identity strings change shape and the carve-out
  correctly declines (a safe hard-fail, not a silently wrong verdict) —
  genuinely outside the real pvxs F8 scenario, which adds its header
  *within* the existing common directory. Closing this properly needs a
  cross-snapshot root computation `compute_extraction_contract`'s current
  one-side-at-a-time design doesn't support, which is its own ADR-level
  design call, not a fifth drive-by carve-out. `--diagnostic-comparison`
  remains the correct workaround.

New regression tests: one gate-level end-to-end test reproducing the real
production invocation shape via `resolve_inferred_header_roots` itself
(proven to fail with `ProfileMismatchError` without the fix), one
composing-carve-outs end-to-end test, seven direct unit tests of
`_include_sequence_is_additive_owned_growth`, and one test pinning the
common-root-shift limitation as an accepted, still-correctly-raising case.
Full fast unit suite green, mypy/ruff clean, 98% branch coverage on
`comparability.py` — this is the final state of all four carve-outs
(scope, header-sequence, unchanged-field handling, include-sequence, and
their composition) as of this branch.

```yaml
# .github/workflows/abi.yml (for epics-base/pvxs)
name: ABI check
on:
  pull_request:
  push:
    tags: ['*']
permissions:
  contents: read
  security-events: write
jobs:
  abi:
    runs-on: ubuntu-24.04
    strategy:
      matrix:
        lib: [libpvxs, libpvxsIoc]
    steps:
      - uses: actions/checkout@df4cb1c069e1874edd31b4311f1884172cec0e10  # v6
        with: { fetch-depth: 0 }
      - name: Build old + new (EPICS Base + both pvxs refs, -g -Og)
        run: ./ci/build-two-refs.sh   # produces old/lib/<arch> and new/lib/<arch>
      - uses: abicheck/abicheck@47826f6f384986d2b0d1e29da1dfe387a38976d3  # claude/pvxs-version-scan-02efc5 HEAD, 2026-07-26 (temporary -- see note below; retarget to a release once one ships this branch's fixes)
        with:
          old-library: old/lib/linux-x86_64/${{ matrix.lib }}.so
          new-library: new/lib/linux-x86_64/${{ matrix.lib }}.so
          old-header: old/include
          new-header: new/include
          include: >-
            ${{ env.EPICS_BASE }}/include
            ${{ env.EPICS_BASE }}/include/os/Linux
            ${{ env.EPICS_BASE }}/include/compiler/gcc
          scope-public-headers: 'true'
          format: sarif
          output-file: abi-${{ matrix.lib }}.sarif
          # A release that adds a new public header (F8 above) needs
          # extra-args: '--diagnostic-comparison' for that one PR, or the
          # step correctly fails closed with a clear message instead of a
          # wrong verdict — this is NOT something to blanket-set by default.
      - uses: github/codeql-action/upload-sarif@7188fc363630916deb702c7fdcf4e481b751f97a  # v4
        if: always()
        with: { sarif_file: abi-${{ matrix.lib }}.sarif }
```

## L3–L5 source-depth scan on `master` — attempted, not completed (documented)

`scan --depth source` on `libpvxs` (master, `--ast-frontend clang`,
`compiledb`-generated 62-entry compile database — matching the first
report's 61-CU count almost exactly, confirming the compile-DB scope itself
was correct, not bloated) did **not** complete within a 3+ minute wall-clock
budget on this 4-core host (RSS climbing past 4.9 GB before being killed);
the first report's equivalent L2/L3/L4/L5 pass on 1.5.2 completed in 129 s,
but that ran with **castxml**, not installed here. `--budget 3m` did not
preempt the run either — the single expensive step (L4 source-ABI replay
compiling/parsing each real `.cpp` TU via `clang -ast-dump=json`) appears to
run past the wall-clock check point rather than yielding mid-step, so
`--budget` bounds *between* phases, not inside one.

**Not root-caused or fixed in this pass** — distinguishing "L4 replay via the
clang frontend is inherently far more expensive per-TU than castxml on a
real C++ template-heavy codebase" from "something is superlinear/pathological
in this specific run" needs a dedicated profiling pass (the same kind of
work the first report's F1/F5b did for the L1/L2 path), which is out of
scope for this validation pass. Documented as a real, reproducible gap: a
clang-only host (no castxml) cannot currently get a practical CI-budget L3–L5
source scan on a library this size. Until profiled, the safe recommendation
for a clang-only pvxs CI runner is to skip the `scan --depth source` job
(keep `compare` for the L1/L2 release gate) or scope it with
`--since`/`--changed-path` to just the PR's changed files rather than the
whole library.

## Scanner/process review — follow-up fixes

A subsequent review of this report's own findings (asked: "what needs
improving in the scanner and its process to work adequately?") turned up
four concrete, tractable follow-ups beyond F8/F9 themselves, each fixed in
this same branch:

- **Grammar-level property test for the F9 classifier.** F9's nine-round
  review cycle found nine different gaps in the same Itanium `<local-name>`
  grammar one real mangled-name example at a time (missing restrict
  qualifier, missing `Sa`-`Sd` substitutions, missing `__gnu_debug`, missing
  recursive lambda nesting, missing user-specializable customization-point
  exclusion, ...) — a pattern that generalizes: a hand-rolled mangling
  classifier expressed as a regex, tested only against examples someone
  happened to think of, will keep finding new gaps one real binary at a
  time. `tests/test_name_classification_properties.py` (Hypothesis,
  `slow`-marked) instead generates the grammar itself — nesting depth,
  every CV/ref-qualifier combination, every recognized stdlib marker, both
  stdlib- and library-owned roots — so a future gap in the same grammar is
  caught structurally instead of waiting for the next real binary.
- **F8 fixed, not left as a documented gap** (see F8 section above,
  updated) — the additive-only header-set carve-out, with its own ADR-050
  D2 subsection and regression tests.
- **`--budget` mid-step preemption gap, partially fixed** (see the L3–L5
  section above, updated) — the loop-driver gap that let the L4 replay's
  serial fallback dispatch further translation units after the deadline had
  already passed is closed; the deeper "is clang-frontend L4 replay
  inherently this expensive, or is something pathological" question is
  explicitly left open, needing a dedicated profiling pass this same-branch
  follow-up is not the right scope for.
- **CLI/Action ergonomics traps hit while writing this report's own
  recommended workflow** — the directory/package `--format sarif` usage
  error now states *why* (single-pair-only) instead of just listing
  alternatives; `action.yml`'s `header` input now warns explicitly that it
  applies to both sides (the exact confusion this report's F8 section hit
  while drafting the recommended workflow); the Versioning/SARIF-recipe
  docs now call out pinning every `uses:` step — not just
  `abicheck/abicheck` — to a commit SHA whenever a job grants
  `security-events: write` or another elevated permission, a gap this
  report's own eighth/ninth review passes found and fixed only for its own
  one-off recommendation, not in the docs a future integrator would
  actually read.

## Status

- **Fixed & tested in this branch:** F9 (a second, differently-mangled
  RTTI-adjacent alignment false positive — Itanium local-name-production
  symbols, scoped to the stdlib-owned subset after PR review); F8
  (scope-comparability gate's additive-only header-set carve-out, ADR-050
  D2, with its own regression tests). Full fast unit suite green, mypy/ruff
  clean.
- **Verified working, no code change needed:** the full 1.4.0→1.5.0→1.5.1→
  1.5.2 tag-to-tag matrix (real, from-scratch EPICS Base R7.0.10 + pvxs
  builds — the acceptance spike's "still owed" step); the abicheck GitHub
  Action's real shell-script layer end-to-end against a real pvxs binary
  pair.
- **Follow-up hardening in this same branch, prompted by a review of this
  report** (see "Scanner/process review — follow-up fixes" below): a
  Hypothesis grammar property test for the F9 classifier (so future mangled-
  name grammar gaps are caught structurally instead of one real binary at a
  time); clearer CLI/Action error messages and docs for the SARIF/directory
  and header-staging traps this pass's own recommended workflow hit; two
  `deadline.check()` insertions narrowing the L3-L5 budget-preemption gap.
- **Documented, not fixed:** the deeper "is clang-frontend L4 replay
  inherently more expensive per-TU than castxml on a template-heavy real
  codebase, or is something pathological" question — needs a dedicated
  profiling pass (mirroring F1/F5b's approach), out of scope for a
  same-branch follow-up; `--diagnostic-comparison` remains available for
  the scope-mismatch cases the F8 carve-out still correctly declines.
- Binaries are not committed (per `validation/` convention); reproduce with
  the commands above.
