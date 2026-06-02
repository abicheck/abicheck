# Configuration Key & Default-Value Review

> **Status:** Review (2026-06), **largely implemented**. Audit of every CLI
> flag, config-file key, and environment variable across all operating modes,
> with an opinionated assessment of redundancy, inconsistency, and surprising
> defaults. The "Do first / Do next / Do later" action list at the end has been
> implemented (see §7 for the per-item status); this document remains the
> rationale of record.

This document answers three questions:

1. **What knobs do we expose, per mode, and what are their defaults?**
2. **Where are defaults and semantics *inconsistent* between modes?** (the part
   that hurts users most)
3. **What can we *remove or consolidate*** without losing real capability?

---

## 1. The surface, at a glance

| Mode | Command(s) | Knob count (approx) | Policy | Severity | Public-surface scoping | Exit-code scheme |
|------|-----------|--------------------:|:------:|:--------:|:----------------------:|------------------|
| Compare | `compare` | ~55 | ✅ `--policy`/`--policy-file` | ✅ `--severity-*` | ✅ **default ON** | 0/2/4 (legacy) **or** 0/1/2/4 (severity) |
| Multi-binary | `compare-release` | ~45 | ✅ `--policy`/`--policy-file` | ✅ `--severity-*` (aggregated) | ✅ **default ON, toggle** | 0/2/4/8 (+severity) |
| App-centric | `appcompat` | ~27 | ✅ `--policy`/`--policy-file` | ✅ `--severity-*` (app-scoped) | ✅ **default ON, toggle** | 0/1/2/4 |
| Snapshot | `dump` | ~30 | ❌ | ❌ | n/a | 0/1/2 |
| ABICC drop-in | `compat check`/`dump` | ~110 (≈24 no-op stubs) | ❌ (hard-wired) | ❌ | ❌ | 0/1/2, 3–11 errors |
| Stack | `stack-check`, `deps` | ~7 each | ❌ | ❌ | n/a | 0/1/4 / 0/1 |
| Baseline | `baseline push/pull/list/delete` | ~8 | ❌ | ❌ | n/a | 0/1 |
| Probe (G2) | `probe run/compare` | ~9 | ✅ `--policy` | ❌ | n/a | 0/2/4 (+3 = incomplete matrix without `--allow-failures`) |
| Debian | `debian-symbols generate/validate/diff` | ~4 | ❌ | ❌ | n/a | 0/1 (+2 = `validate` symbols mismatch) |
| Suggest | `suggest-suppressions` | 2 | ❌ | ❌ | n/a | 0 |
| MCP server | `abicheck-mcp` | 3 (env + 3 CLI) | ❌ | ❌ | inherits API | n/a |
| Python API | `abicheck.service` | function kwargs | via args | n/a | ✅ **default ON** | n/a |

Total unique flags across the tool: **~220** (counting per-side / alias variants
once). `compat check` alone carries ~110, of which ~24 are intentional no-op
ABICC stubs.

The headline: **the same conceptual knob has different defaults, different
spellings, and sometimes doesn't exist at all depending on which subcommand you
type.** That is the core UX problem, far more than the raw count.

---

## 2. Cross-mode inconsistencies — ranked by user impact

### 🔴 2.1 `--scope-public-headers` had *opposite* defaults across modes ✅ resolved

This was the single most confusing thing in the whole surface. The table below
shows the **original** state that motivated the fix:

| Entry point | Original flag form | Original default |
|-------------|-----------|---------|
| `compare` | `--scope-public-headers/--no-scope-public-headers` (toggle) | **ON** |
| `compare-release` | `--scope-public-headers` (plain `is_flag`, no `--no-`) | **OFF** |
| `appcompat` | *no CLI flag* — always-on via `compare()`'s `scope_to_public_surface=True` default | **ON (forced)** |
| `run_compare()` (Python API) | `scope_to_public_surface=True` | **ON** |

The problem: a user who ran `compare` saw findings filtered to the public-header
surface, then ran `compare-release` on the same libraries and saw a *different,
larger* finding set with no way to infer why; `appcompat` scoped always but gave
no toggle — three behaviors for one conceptual knob.

**Resolution (implemented, see §7 do-first #1):** all three comparison commands now expose
the **same `--scope-public-headers/--no-scope-public-headers` toggle**, and
`compare-release` was flipped to **default ON** to match `compare` and the Python
API (`cli_compare_release.py`, `cli_appcompat.py`). The default flip is a breaking
change for `compare-release` callers that relied on unscoped output — pass
`--no-scope-public-headers` to restore.

### 🔴 2.2 Severity system was `compare`-only ✅ resolved (additive + explicit)

Originally `--severity-preset` and the four `--severity-*` category flags were
**`compare`-only**. `compare-release` and `appcompat` — both of which run the
same diff engine — had no severity control and fell back to verdict-based exit
codes. Worse, on `compare` the scheme switched **silently**: passing *any*
`--severity-*` flag moved you from the 0/2/4 verdict scheme to the 0/1/2/4
severity scheme with no signal.

**Resolution (implemented, see §7 do-first #2):**
- The `--severity-preset`/`--severity-*` option group was added to
  `compare-release` (aggregated across per-library, bundle, and matrix findings,
  honoring per-library `--policy-file` overrides; removed-library exit `8` still
  wins) and `appcompat` (full-compare mode only; app-scoped to `breaking_for_app`,
  with missing required symbols/versions floored at `4`).
- `compare` now **prints the active exit-code scheme to stderr** for human
  formats, so the switch is no longer silent. Exit-code numbers are unchanged.

It was *not* made "always severity-aware", because a literal legacy-equivalent
preset can't be expressed through the four-category model: API_BREAK and RISK
share `POTENTIAL_BREAKING` while the verdict path maps `COMPATIBLE_WITH_RISK`→0,
so `compute_exit_code()` with any single preset would change RISK handling
(`severity.py:185`). Making the scheme *visible* avoids that regression while
removing the "which scheme am I in?" confusion.

### 🟠 2.3 Policy availability is uneven

`--policy` + `--policy-file` exist on `compare`, `compare-release`, `appcompat`,
and `probe compare`. They are **absent** on `compat` (hard-wired `strict_abi`,
by ABICC-parity design) and on `dump`/`stack-check`/`deps`. The asymmetry is
defensible per-mode, but it isn't surfaced anywhere; a user migrating an ABICC
pipeline to `compat` cannot apply the `sdk_vendor` policy they use elsewhere.

**Recommendation:** Document the policy matrix in one place (the table in §1 is a
start). Consider a `compat`-side opt-in (e.g. mapping `-s/--strict` and a future
`--policy` passthrough) so vendor policies aren't stranded.

### 🟠 2.4 Exit-code schemes differ per mode

`0/2/4` (compare legacy), `0/1/2/4` (compare severity, appcompat),
`0/2/4/8` (compare-release, +8 = removed library), `0/1/4` (stack-check),
`0/1` (deps/baseline), `0/2/4` + `3` (probe; 3 = incomplete matrix),
`0/1/2` (debian-symbols; 2 = `validate` mismatch), `0/1/2 + 3–11` (compat).
Two collisions stand out:
- `1` means *severity error* in `compare`, *tool error* in `appcompat`, *WARN*
  in `stack-check`, *missing deps* in `deps`. Same number, four meanings.
- `2` means *API_BREAK* in the comparison family, but *symbols mismatch* in
  `debian-symbols validate` and *incompatible change* in `compat` — and `3`
  means *incomplete matrix* in `probe` but a *tool/error* class in `compat`.

**Recommendation:** This is partly inherent (different tasks), but the exit-`1`
collision is avoidable. At minimum, the
[exit-codes reference](../reference/exit-codes.md) should carry a single
decision-tree covering every command, and `1` should not mean both "tool
crashed" and "a real finding" within the comparison family.

### ✅ 2.5 `--annotate` stream is already consistent (verified — non-issue)

Both `compare` (`cli.py:1255`, `_maybe_emit_annotations`) and `compare-release`
(`cli_compare_release.py:384`) emit GitHub annotations to **stderr**
(`click.echo(..., err=True)`). No divergence; no action needed. (An earlier draft
of this review incorrectly claimed `compare-release` used stdout.)

### 🟡 2.6 Suppression: extra plaintext inputs in `compat`, but YAML is shared

`compare`/`compare-release`/`appcompat` take a YAML `--suppress` file. `compat`
*also* accepts the same YAML `--suppress` (`compat/cli.py:1056`, loaded via
`SuppressionList.load()` at `1527` and merged in `_build_compat_suppression()`),
**on top of** the ABICC plaintext `-symbols-list`/`-types-list`/`-skip-symbols`
inputs. So a team **can** share one YAML suppression source across `compat` and
`compare` today.

**Recommendation:** No bridge needed — just make sure the
[from-abicc guide](../user-guide/from-abicc.md) documents that `--suppress
<yaml>` works in `compat` too, so users migrating off ABICC plaintext lists know
the shared YAML path exists.

### 🟡 2.7 "No headers" means three different things

- `compare` without `-H`: symbols-only fallback + warning.
- `dump` without `-H`: DWARF-only if debug info present.
- `appcompat --check-against` / `--list-required-symbols`: the per-side
  `--old-*`/`--new-*` header & include flags are **rejected** with a
  `UsageError` (`cli_appcompat.py`), and plain `-H/--header` / `-I/--include`
  are accepted but not used (the library ABI is not extracted in these modes).

**Resolution (implemented, see §7 do-later #3):** the weak/list `appcompat`
branches now **warn** on stderr when `-H`/`-I` are supplied (instead of silently
ignoring them), so users aren't misled about what surface is analyzed. The
remaining inconsistency — that "no headers" means symbols-only fallback in
`compare` vs DWARF-only in `dump` — is documentation-only and surfaced in the
respective `--help`.

---

## 3. Redundant / removable / consolidatable keys

### 3.1 ABICC `compat` no-op stubs (~24 flags) — *keep hidden; already handled well*

`compat check` accepts ~24 hidden P2 stub flags (`-mingw-compatible`, `-static`,
`-ext`, `-quick`, `-force`, `-tolerance`, `-count-symbols`, `-sort`, `-xml`, …)
that do **nothing**, plus `-params`, `-app`, and `-filter` that are accepted but
not yet applied. Drop-in parity is a real feature, so deleting them would break
copy-pasted ABICC command lines.

Importantly, the analysis-implying ones are **not** silently ignored:
`_emit_compat_info_notes()` already prints a stderr note for `-filter`/`-params`/
`-app` (and `-count-symbols`/`-count-all-symbols`) — *"Note: -app … is accepted
for compatibility (not yet applied)."* (`compat/cli.py:1297-1309`, `1405-1422`,
quiet-respecting via `_do_echo`).

**Recommendation:** No action needed — keep the pure stubs hidden, and the
existing info-notes already prevent the "silent no-op" correctness trap. The only
optional polish would be pointing users at `appcompat` (for `-app`) and
`--suppress` (for `-filter`) in those note strings.

### 3.2 `--compile-db` is a documented alias of `-p/--build-dir`

`cli.py` defines both for the same target (`dump`). It is **not** purely
internal — `--compile-db` is documented as the explicit `compile_commands.json`
path (`cli-usage.md:125`) and appears in ADR examples, so it's part of the
public surface.

**Recommendation:** Don't remove it (that would break documented command lines).
The most you'd want is to **deprecate/hide it from `--help`** in favor of the
canonical `-p/--build-dir`, keeping it functional as an accepted alias. Low
priority — the redundancy is cosmetic.

### 3.3 The report-content "show-*" family overlaps

`compare` carries: `--show-redundant`, `--show-filtered`, `--show-impact`,
`--show-only`, `--report-mode {full,leaf}`, `--recommend`, `--stat`
(plus `appcompat --show-irrelevant`). Two genuine overlaps:

- `--report-mode leaf` and `--show-impact` both surface "root change → affected
  interfaces" grouping. A user must learn both to discover they're related.
- `--show-redundant` (un-filter derived changes) vs `--report-mode full/leaf`
  (group vs flat) vs `--show-filtered` (out-of-surface audit) are three separate
  axes that read as one "verbosity" concept to newcomers.

**Recommendation:** Don't delete capability, but consider folding
`--show-impact` into `--report-mode` (e.g. `--report-mode impact`) and
documenting the three axes (redundancy / grouping / surface) as an explicit
"what gets shown" section rather than seven scattered flags.

### 3.4 Four debug-format flags where two would do

`--btf`, `--ctf`, `--dwarf`, and `--dwarf-only` coexist on `compare`/`dump`.
`--dwarf` (select DWARF as the ELF debug format) and `--dwarf-only` (force debug
info as primary over headers) are different but read as near-synonyms and are a
known confusion point.

**Recommendation:** Collapse the format *selector* into one option:
`--debug-format {auto,dwarf,btf,ctf}` (default `auto`), and keep `--dwarf-only`
as the orthogonal "ignore headers" switch — but rename it to something that
doesn't collide, e.g. `--ignore-headers` / `--binary-truth`.

### 3.5 `--annotate-additions` could be inferred

It is only meaningful with `--annotate` and errors/no-ops otherwise. Minor, but
it's a flag that exists only to qualify another flag.

**Recommendation:** Acceptable to keep; low priority.

### 3.6 Version-label default drift

`--old-version`/`--new-version` default to the literal strings `"old"`/`"new"`;
`dump --version` defaults to `"unknown"`. Harmless but inconsistent.

**Recommendation:** Make `dump --version` default to `"unknown"` and the
compare-side default to the input filename stem when resolvable, rather than the
opaque `"old"`/`"new"` — more useful in reports for zero extra typing.

---

## 4. Defaults that may surprise users

| Knob | Current default | Why it surprises | Suggested |
|------|-----------------|------------------|-----------|
| `compare --scope-public-headers` | **ON** | Findings are silently filtered out of the report | Keep ON, but always print the filtered count (it does on resolve-fail only) |
| `compare-release -j/--jobs` | ~~1 (serial)~~ → **0 (auto)** | Was serial by default, making multi-library releases slow | ✅ Implemented: defaults to `0` (auto-detect CPUs); parallel output is deterministic (matched-key order) |
| `compare --demangle` | **OFF** | Human-readable output shows mangled `_ZN…` names by default | ✅ Implemented: default ON for `markdown`/`review`; `json`/`sarif`/`html` keep mangled (HTML can't be safely string-demangled) |
| `--lang` | **`c++`** | C libraries parsed as C++ can mis-parse | Reasonable default; add autodetect note |
| `compat -report-format` | **`html`** | A CLI invocation writes an HTML file by default | ABICC parity — keep, but document |
| `suggest-suppressions --expiry-days` | **180** | Generated suppressions silently expire in ~6 months | Fine, but state it in the generated file header |
| `baseline --registry` | **`.abicheck/baselines`** in cwd | Writes into the current directory tree | Document; consider `$XDG_DATA_HOME` option |
| MCP `ABICHECK_MCP_TIMEOUT` | **120 s** | Large libs may exceed it | Fine; documented |
| MCP `ABICHECK_MCP_MAX_FILE_SIZE` | **500 MB** | Silently rejects bigger inputs | Document the limit in the error |

---

## 5. Environment variables (complete list)

| Variable | Default | Effect |
|----------|---------|--------|
| `GITHUB_ACTIONS` | unset | Gates annotation/step-summary emission (`annotations.py:261`) |
| `GITHUB_STEP_SUMMARY` | unset | Path for CI job-summary markdown |
| `DEBUGINFOD_URLS` | unset | debuginfod servers (opt-in via `--debuginfod`) |
| `_NT_SYMBOL_PATH` | unset | Windows PDB symbol search path |
| `XDG_CACHE_HOME` | `~/.cache` | Snapshot/debug cache root |
| `LOCALAPPDATA` | `~/AppData/Local` | Windows castxml cache root |
| `SYCL_PI_PLUGINS_DIR`, `SYCL_UR_ADAPTERS_DIR` | unset | SYCL plugin discovery |
| `ABICHECK_MCP_TIMEOUT` | `120` | MCP per-call timeout (s) |
| `ABICHECK_MCP_MAX_FILE_SIZE` | `524288000` | MCP max input bytes |

These are well-scoped and follow platform conventions (XDG / LOCALAPPDATA). No
changes recommended beyond documenting the two `ABICHECK_MCP_*` knobs in the MCP
guide.

---

## 6. Config-file keys (policy / suppression / severity)

**Policy file** (`--policy-file`, `policy_file.py`): `base_policy`
(default `strict_abi`), `overrides` (ChangeKind → `break|warn|risk|ignore`),
`frozen_namespaces` (glob patterns that block downgrades). Clean, minimal, no
redundancy. Unknown `base_policy` values are **rejected** with a `PolicyError`
listing the valid names (`policy_file.py:150-154`); unknown `overrides` slugs are
warned-and-skipped (`policy_file.py:177-182`, intentional typo tolerance). The
silent `strict_abi` fallback that exists in the low-level `get_policy()` helper
(`checker_policy.py:715`) is **not** reachable through `--policy-file`, because
`PolicyFile.load()` validates the name first. No change needed here.

**Suppression file** (`--suppress`, `suppression.py`): selectors `symbol`,
`symbol_pattern`, `type_pattern`, `member_name`, `change_kind`, `namespace`,
`source_location`, plus `reason`/`label`/`expires`. Rich but each selector earns
its place; unknown keys are *rejected* (good — catches typos). No removal
candidates. The built-in `audit()` (stale/expired/high-risk) is a strength.

**Severity** (`severity.py`): presets `default`/`strict`/`info-only` over four
categories. Coherent. The only issue is reach (§2.2), not the schema.

---

## 7. Prioritized action list — implementation status

**Do first (correctness / least-surprise):**
1. ✅ **Done.** `--scope-public-headers/--no-scope-public-headers` toggle is now
   on `compare`, `compare-release` (flipped to **default ON**), and `appcompat`
   (which previously had no toggle); the Python API default is unchanged (ON).
   (§2.1)
2. ✅ **Done (explicit-messaging form).** `compare` now prints the active
   exit-code scheme to stderr instead of switching silently, and `--severity-*`
   /`--severity-preset` were added to `compare-release` and `appcompat`. Exit
   numbers are unchanged: a literal "legacy preset" can't be reproduced through
   the severity engine because API_BREAK and RISK share `POTENTIAL_BREAKING`
   while the verdict path maps `COMPATIBLE_WITH_RISK`→0, so forcing
   always-severity-aware would change RISK handling. (§2.2)

**Do next (consolidation):**
3. ✅ **Done (additive).** Added `--debug-format {auto,dwarf,btf,ctf}` to
   `compare`/`dump`; the legacy `--btf/--ctf/--dwarf` flags are hidden but still
   functional. `--dwarf-only` was **not** renamed (a rename breaks documented
   command lines; left as-is). (§3.4)
4. ✅ **Done.** `--report-mode` gained `impact` (sugar for `full` +
   `--show-impact`); `--show-impact` still works standalone. (§3.3)
5. ✅ **Done.** `--compile-db` is hidden from `--help` but still functional as an
   alias of `-p/--build-dir`. (§3.2)

**Do later (defaults polish):**
6. ✅ **Done.** `compare-release -j` defaults to `0` (auto-detect CPUs). (§4)
7. ✅ **Done.** `compare --demangle` defaults ON for `markdown`/`review` (the
   formats whose renderer post-processes symbols through `demangle_text`), OFF
   for `json`/`sarif` and `html` (HTML is rendered structurally and is never
   demangled — demangling its string would inject unescaped `<`/`>`/`&`). (§4)
8. ✅ **Done.** The weak/list `appcompat` branches now warn when `-H`/`-I` are
   supplied (instead of silently ignoring them). (§2.7)

**Keep as-is (don't remove):**
- ABICC P2 no-op stubs (hidden, harmless, real drop-in value).
- Suppression selector richness and `audit()`.
- Policy-file schema.
- Per-side `--old-*`/`--new-*` header/include overrides (genuinely needed).
- Provenance flags (`--git-tag`, `--build-id`, `--no-git`).

Nothing here is a *capability* worth deleting outright; the wins are in
**unifying defaults**, **renaming colliding flags**, and **not silently
ignoring** flags that imply work.
