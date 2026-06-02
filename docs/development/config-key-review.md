# Configuration Key & Default-Value Review

> **Status:** Review / proposal (2026-06). Audit of every CLI flag, config-file
> key, and environment variable across all operating modes, with an opinionated
> assessment of redundancy, inconsistency, and surprising defaults. Nothing here
> changes behavior on its own — it is a decision input for trimming the surface.

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
| Multi-binary | `compare-release` | ~40 | ✅ `--policy`/`--policy-file` | ❌ | ✅ **default OFF** | 0/2/4/8 |
| App-centric | `appcompat` | ~22 | ✅ `--policy`/`--policy-file` | ❌ | ❌ (absent) | 0/1/2/4 |
| Snapshot | `dump` | ~30 | ❌ | ❌ | n/a | 0/1/2 |
| ABICC drop-in | `compat check`/`dump` | ~110 (≈24 no-op stubs) | ❌ (hard-wired) | ❌ | ❌ | 0/1/2, 3–11 errors |
| Stack | `stack-check`, `deps` | ~7 each | ❌ | ❌ | n/a | 0/1/4 / 0/1 |
| Baseline | `baseline push/pull/list/delete` | ~8 | ❌ | ❌ | n/a | 0/1 |
| Probe (G2) | `probe run/compare` | ~9 | ✅ `--policy` | ❌ | n/a | 0/2/4 |
| Debian | `debian-symbols generate/validate/diff` | ~4 | ❌ | ❌ | n/a | 0/1 |
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

### 🔴 2.1 `--scope-public-headers` has *opposite* defaults across modes

This is the single most confusing thing in the whole surface.

| Entry point | Flag form | Default | Source |
|-------------|-----------|---------|--------|
| `compare` | `--scope-public-headers/--no-scope-public-headers` (toggle) | **ON** | `cli.py:1519-1525` |
| `compare-release` | `--scope-public-headers` (plain `is_flag`, no `--no-`) | **OFF** | `cli_compare_release.py:1046` |
| `appcompat` | *flag does not exist* | n/a | `cli_appcompat.py` |
| `run_compare()` (Python API) | `scope_to_public_surface=True` | **ON** | `service.py:615` |

Consequences:
- A user who runs `compare` sees findings silently filtered to the public-header
  surface (with a one-line warning if it can't resolve, `cli.py:1404`), then runs
  `compare-release` on the same libraries and sees a *different, larger* finding
  set — for no reason they can infer.
- `compare` exposes `--no-...` to turn it off; `compare-release` has no way to
  turn it *on* with a negation, and no `--no-bundle`-style symmetry.
- `appcompat`, which is *also* a comparison, has no scoping concept at all.

**Recommendation:** Pick one default (ON is the better UX — it's what
distinguishes this tool from raw `abidiff` noise), make all three comparison
commands use the **same toggle flag and the same default**, and add the toggle
to `appcompat`. If `compare-release` must stay OFF-by-default for backwards
compatibility, at minimum give it the `/--no-` toggle form and document *why*
it differs.

### 🔴 2.2 Severity system exists only on `compare`

`--severity-preset` and the four `--severity-*` category flags
(`cli.py:1486-1507`) are **`compare`-only**. `compare-release` and `appcompat`
— both of which run the exact same diff engine — have no severity control and
fall back to verdict-based exit codes.

This also creates the **dual-path exit-code behavior** that only `compare` has
(`cli.py:1294-1309`, `_resolve_severity` at `1171`): pass *any* `--severity-*`
flag and you silently switch from the 0/2/4 verdict scheme to the 0/1/2/4
severity scheme. Users cannot tell from the command which scheme they're in.

**Recommendation:** Either (a) promote severity config to a shared option group
used by `compare`, `compare-release`, and `appcompat`, or (b) if severity is
meant to be the *one true* gating mechanism, deprecate the legacy verdict-exit
path and always run severity-aware (defaulting to the `default` preset, which
reproduces 0/2/4). Today's "implicit mode switch on first `--severity-*` flag"
is the worst of both worlds.

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
`0/1` (deps/baseline), `0/1/2 + 3–11` (compat). And `1` means *severity error*
in `compare`, *tool error* in `appcompat`, *WARN* in `stack-check`, *missing
deps* in `deps`. Same number, four meanings.

**Recommendation:** This is partly inherent (different tasks), but the collision
on exit `1` is avoidable. At minimum, the [exit-codes reference] should carry a
single decision-tree, and `1` should not mean both "tool crashed" and "a real
finding" within the comparison family.

### 🟡 2.5 `--annotate` writes to different streams

`compare` emits GitHub annotations to **stderr** (`cli.py:1574`,
`_maybe_emit_annotations`); `compare-release` emits to **stdout**
(`cli_compare_release.py:384`). CI consumers piping output will see different
behavior.

**Recommendation:** Standardize on stderr (annotations are diagnostics, not the
report payload).

### 🟡 2.6 Suppression input formats differ

`compare`/`compare-release`/`appcompat` take a YAML `--suppress` file;
`compat` reuses ABICC plaintext `-symbols-list`/`-types-list`/`-skip-symbols`.
A team cannot share one suppression source across `compat` and `compare`.

**Recommendation:** Acceptable for drop-in parity, but `compat` should also
accept `--suppress <yaml>` (it already does per agent inventory at
`compat/cli.py`) — make sure that's documented as the bridge.

### 🟡 2.7 "No headers" means three different things

- `compare` without `-H`: symbols-only fallback + warning.
- `dump` without `-H`: DWARF-only if debug info present.
- `appcompat --check-against` / `--list-required-symbols`: headers *rejected*.

**Recommendation:** Unify the help text and emit a consistent one-line note on
each path describing what surface is actually being analyzed.

---

## 3. Redundant / removable / consolidatable keys

### 3.1 ABICC `compat` no-op stubs (~24 flags) — *keep hidden, but stop silently lying*

`compat check` accepts ~24 hidden P2 stub flags (`-mingw-compatible`, `-static`,
`-ext`, `-quick`, `-force`, `-tolerance`, `-count-symbols`, `-sort`, `-xml`, …)
that do **nothing**, plus `-params`, `-app`, and `-filter` that are accepted but
**not applied**. Drop-in parity is a real feature, so deleting them would break
copy-pasted ABICC command lines. But `-app` (application portability) and
`-filter` (skip rules) *look* functional and are silently ignored.

**Recommendation:** Keep the pure stubs hidden (they're harmless). For
`-app`/`-filter`/`-params`, emit a one-line stderr warning: *"accepted for ABICC
compatibility but not applied; use `appcompat` / `--suppress` instead."* Silent
no-ops on flags that imply analysis are a correctness trap.

### 3.2 `--compile-db` is a pure alias of `-p/--build-dir`

`cli.py` defines both for the same target (`dump`). Aliases add doc surface for
no capability.

**Recommendation:** Keep `-p/--build-dir` (the documented form), demote
`--compile-db` to a hidden alias or drop it.

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
| `compare-release -j/--jobs` | **1 (serial)** | Multi-library releases are slow by default; `0` = auto exists but isn't the default | Default to `0` (auto) or document prominently |
| `compare --demangle` | **OFF** | Human-readable output shows mangled `_ZN…` names by default | Default ON for `markdown`/`html`/`text`; keep mangled for `json`/`sarif` |
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
redundancy. Unknown `base_policy` falls back to `strict_abi` **silently**
(`checker_policy.py:715`) — recommend warning on unknown values.

**Suppression file** (`--suppress`, `suppression.py`): selectors `symbol`,
`symbol_pattern`, `type_pattern`, `member_name`, `change_kind`, `namespace`,
`source_location`, plus `reason`/`label`/`expires`. Rich but each selector earns
its place; unknown keys are *rejected* (good — catches typos). No removal
candidates. The built-in `audit()` (stale/expired/high-risk) is a strength.

**Severity** (`severity.py`): presets `default`/`strict`/`info-only` over four
categories. Coherent. The only issue is reach (§2.2), not the schema.

---

## 7. Prioritized action list

**Do first (correctness / least-surprise):**
1. Unify `--scope-public-headers` default + flag form across `compare`,
   `compare-release`, `appcompat` (§2.1).
2. Make the legacy-vs-severity exit-code switch explicit, or always run
   severity-aware (§2.2).
3. Warn (don't silently ignore) on `compat -app`/`-filter`/`-params` (§3.1).
4. Standardize `--annotate` on stderr (§2.5).

**Do next (consolidation):**
5. Collapse `--btf/--ctf/--dwarf` into `--debug-format`; rename `--dwarf-only`
   (§3.4).
6. Fold `--show-impact` into `--report-mode`; document the three "what's shown"
   axes (§3.3).
7. Hide/drop `--compile-db` alias (§3.2).

**Do later (defaults polish):**
8. `compare-release -j` default `0` (auto) (§4).
9. `--demangle` default ON for human formats (§4).
10. Warn on unknown `base_policy` (§6); document `ABICHECK_MCP_*` (§5).

**Keep as-is (don't remove):**
- ABICC P2 no-op stubs (hidden, harmless, real drop-in value).
- Suppression selector richness and `audit()`.
- Policy-file schema.
- Per-side `--old-*`/`--new-*` header/include overrides (genuinely needed).
- Provenance flags (`--git-tag`, `--build-id`, `--no-git`).

Nothing here is a *capability* worth deleting outright; the wins are in
**unifying defaults**, **renaming colliding flags**, and **not silently
ignoring** flags that imply work.
