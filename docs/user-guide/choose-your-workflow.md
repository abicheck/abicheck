---
doc_type: hub
audience:
  - library-maintainer
  - ci-owner
level: beginner
summarizes:
  - evidence-model
lifecycle: active
generated: false
---

# Choose Your Workflow

This is the **decision guide**. It answers a single question:

> *"I have **this** artifact, **this** configuration, and **this** problem —
> what command and options should I run?"*

The reference pages (linked throughout) explain every flag in depth. This page
is the front door: find the row that matches your situation, run the **minimum
command**, and reach for the **stronger / production command** when you need
more confidence or a CI gate.

If you only read one thing: **`abicheck compare old new` is the default
workflow.** Everything else on this page is a refinement of it for a specific
artifact layout, accuracy target, or CI policy.

---

## 1) The workflow chooser — what are you comparing?

Pick the row that matches what you physically have on disk and what you want to
know. Run the **minimum command** first; reach for the **stronger / production
command** when you need more confidence or a CI gate.

| Your situation | Minimum command | Stronger / production command |
|---|---|---|
| One shared library — does v2 break v1 consumers? | `abicheck compare libv1.so libv2.so` | `abicheck compare libv1.so libv2.so --header old=include/v1/ --header new=include/v2/` — the primary flow |
| Same public header for both versions | `abicheck compare libv1.so libv2.so -H include/foo.h` (`-H include/` scans a directory recursively) | When compiler flags affect the ABI, capture build context at dump time (`abicheck dump … -H include/foo.h -p build/`) and compare the snapshots |
| No headers at all | `abicheck compare libv1.so libv2.so` | Binary-only fallback is weaker (see [the input-quality ladder](#2-how-much-accuracy-do-you-need)); add debug info via `--debug-root old=old-debug --debug-root new=new-debug` |
| Stripped production binaries | `abicheck compare old.so new.so --debug-root old=old-debug --debug-root new=new-debug` (or `--debuginfod` to fetch by build-id) | Also pass public headers (`-H`) for highest confidence |
| A CI baseline vs a fresh build | `abicheck dump libfoo.so -H include/ -o baseline.json`, then `abicheck compare baseline.json build/libfoo.so --header new=include/` | Store baselines in GitHub Releases, the repo, the Actions cache, or artifact storage — see [Storing Baselines](baseline-storage.md) |
| A PR with source/build context (catch source-only & build-flag breaks) | `abicheck scan build/libfoo.so -H include/ --sources . --against baseline.json --since origin/main` | One orchestrator over dump/compare: always-on pattern + cross-source checks plus the pinned L3/L4/L5 level — see [Source & Build Data](../concepts/build-source-data.md) and the [GitHub Action: Source Scans](github-action-source-scans.md) |
| Build emits source facts in parallel (combine into one baseline) | `abicheck compare old.so new.so --build-info old=abicheck_inputs/v1 --build-info new=abicheck_inputs/v2` (also auto-detects an `abicheck_inputs/` pack alongside each input with no flag at all) | No standalone merge step — `dump`/`compare` auto-ingest each side's embedded or out-of-band build/source pack directly |
| Two snapshots (offline / air-gapped) | `abicheck compare old.json new.json` | No headers/castxml/network needed — everything is baked into the snapshots |
| Several DSOs shipped together | `abicheck compare release-1.0/ release-2.0/ -H include/` (per-library results on all platforms; the cross-library bundle/dependency-skew analysis is **Linux/ELF only**) | Add `--manifest` only for template instantiations, dlsym/plugin contracts, internal stable exports, or symbol-version promises |
| RPM / Deb / tar / conda / wheel packages | `abicheck compare old.rpm new.rpm` | Add `--debug-info old=old-debuginfo.rpm --debug-info new=new-debuginfo.rpm` (debuginfo packages) and `--devel-pkg old=old-devel.rpm --devel-pkg new=new-devel.rpm` (header/devel packages) where available |
| An application + a library upgrade | `abicheck compare libfoo.so.1 libfoo.so.2 --used-by ./myapp` | Add `-H include/`; repeatable for several application binaries; OLD/NEW may be real library binaries or JSON snapshots carrying binary evidence |
| A host that `dlopen`s plugins | `abicheck compare plugin.v1.so plugin.v2.so --required-symbol plugin_init` | Use `--required-symbols host.syms --policy plugin_abi` for a whole host-contract file |
| Will this binary load in this sysroot / rootfs? | `abicheck deps tree ./app --sysroot /rootfs` | `abicheck deps tree ./app` alone checks the dependency tree resolves |
| Two sysroots / container images to compare | `abicheck deps compare usr/bin/app --old-root /old-root --new-root /new-root` | Per-library ABI diff across the whole transitive dependency stack |
| Only a static `.a` / `.lib` archive | *(unsupported directly)* | Extract members (`ar x libfoo.a`) and compare the `.o` objects, or compare a shared library built from the same sources — see [Limitations](../concepts/limitations.md#static-import-library-archives-a-lib) |

`compare` auto-detects each input: `.so` files are dumped on the fly, `.json`
snapshots are loaded directly — you can mix them freely. Deeper references:
[CLI Usage](cli-usage.md), [Tool Modes](tool-modes.md),
[Multi-Binary Releases](multi-binary.md),
[Application Compatibility](appcompat.md), [Plugin Systems](plugin-systems.md).

The rest of this page covers the other three decisions, in the order you'll
meet them: **how much accuracy** you need (§2), **how CI should behave** (§3),
and **which report** to produce (§4).

---

## 2) How much accuracy do you need?

The single biggest lever on what abicheck can *prove* is the quality of the
inputs you give it — its five additive evidence layers, **L0–L4**. More
evidence catches more breaks. Start at the layer your artifacts allow, and add
more when you need more confidence. (The `scan` docs also use a sixth code,
**`L5`** — the source graph abicheck *derives* from L3/L4; you never provide it.
See [Evidence & Detectability](../concepts/evidence-and-detectability.md).) For a
concrete, side-by-side look at *what each layer actually sees* on one example —
and where each one goes blind — see the
[level-by-level walk-through](../concepts/what-each-level-sees.md).

| Layer | Inputs | Confidence | What it newly catches |
|:--:|---|---|---|
| **L0** | Binaries only | **Low** | Symbol add/remove, SONAME/version changes, basic metadata |
| **L1** | + debug info | **Medium** | Struct layout, field offsets, enum values, calling convention, emitted-ABI type changes |
| **L2** | + headers | **High** | Declared public API surface, source-level API breaks, inline/template-related surface |
| **L3** | + build flags (`-p build/`) | **Higher** | The exact ABI-affecting flags the library was built with (`-std`, `_GLIBCXX_USE_CXX11_ABI`, `-fvisibility`, …) |
| **L4** | + sources (build/source pack) | **Best** | Facts that never reach the binary: macro/`constexpr` values, default-argument values, uninstantiated templates |

abicheck reports the **artifact** depth it reached (L0–L2) as the
**`evidence_tier`** field (`elf_only` → `dwarf_aware` → `header_aware`) so you
can calibrate trust in any given run; build/source evidence (L3/L4) is reported
separately in the evidence-coverage table rather than promoting this scalar. See
[Output Formats → Analysis confidence and evidence
tier](output-formats.md#analysis-confidence-and-evidence-tier), the per-layer
[Tool Modes](tool-modes.md#abicheck-native-modes-by-evidence-source-l0l4)
reference, and [Evidence &
Detectability](../concepts/evidence-and-detectability.md) for the full
explanation of why each source changes what abicheck can prove.

**Rules of thumb:**

- **No `castxml`?** Drop the header flags and abicheck falls back to
  DWARF/symbols analysis. It still works — it just catches less.
- **Stripped binaries?** Point abicheck at separate debug files with
  `--debug-root old=` / `--debug-root new=`, or fetch them by build-id with
  `--debuginfod`. See [Evidence, Build-Context, and Debug Flags → Debug-info
  resolution](dump-compare-flags.md#debug-artifact-resolution).
- **Compiler flags affect the ABI** (e.g. `-D` macros that change struct
  layout)? Capture the build context at **dump** time with
  `abicheck dump … -p build/` / `--compile-db` so the header AST is parsed the
  way it was actually compiled, then compare the resulting snapshots. (These
  build-context flags live on `dump`, not `compare`.)

---

## 3) How should CI behave? — policy recipes

abicheck separates two independent questions: **what fails the build** (verdict
/ severity / exit code) and **what appears in the report** (display filtering).
Report filtering with `--show-only` is display-only — it never changes the
verdict or exit code.

### Failure policy (controls the exit code)

| Desired behavior | CLI | GitHub Action |
|---|---|---|
| Report everything, never fail | `--severity-preset info-only` | `fail-on-breaking: false` + upload the report |
| Fail only on **binary ABI** breaks | `--severity-preset info-only --severity-abi-breaking error` | `fail-on-breaking: true`, `fail-on-api-break: false` |
| Fail on ABI **and** source/API breaks | default verdict gate, or explicit `--severity-*` | `fail-on-breaking: true`, `fail-on-api-break: true` |
| Fail on accidental **API additions** too | `--severity-addition error` | `severity-addition: error` |
| Everything is an error (strictest) | `--severity-preset strict` | `severity-preset: strict` |

> **GitHub Action note:** the `severity-preset` / `severity-addition` inputs
> apply to `compare` mode's exit code regardless of whether `old-library`/
> `new-library` are a single pair or directories/packages — the Action forwards
> them and recognizes the resulting `SEVERITY_ERROR` verdict (exit code `1`)
> either way, since the underlying `compare` CLI command's severity-aware exit
> scheme already covers both.

```bash
# Report everything, fail ONLY on binary ABI breaks
# (i.e. source/API breaks are allowed through)
abicheck compare old.json new.so \
  --header new=include/ \
  --severity-preset info-only \
  --severity-abi-breaking error

# Fail on binary ABI breaks AND new public API additions
abicheck compare old.json new.so \
  --header new=include/ \
  --severity-addition error
```

### Display filter (does **not** change verdict or exit code)

```bash
# Show only additions in a review report — verdict and exit code unchanged
abicheck compare old.json new.so \
  --header new=include/ \
  --show-only compatible,added
```

Full reference: [Severity Configuration](severity.md). The default model is
already "report additions but don't fail on them" — additions are classified in
the `addition` category, which defaults to `info`.

---

## 4) Which report? — output by audience

| You need… | Format | Best for |
|---|---|---|
| A human-readable summary in a PR or terminal | `--format markdown` (default) | Code review, quick triage |
| A standalone shareable report | `--format html` | Release artifacts, ABICC migration |
| Machine-readable structured data | `--format json` | CI logic, custom gates, agents |
| GitHub Code Scanning / SAST | `--format sarif` | Inline PR annotations, Security tab |
| CI test dashboards | `--format junit` | GitLab CI, Jenkins, Azure DevOps, CircleCI |

For large diffs, add `--report-mode leaf --show-impact` to group derived
changes under their root cause. Full reference:
[Output Formats](output-formats.md).

> **Bundle/package compare formats are narrower:** a release/bundle `compare`
> (directory/package inputs) emits only `markdown`, `json`, and `junit` —
> **not** `sarif` or `html`. Those two formats apply to single-library
> `compare`. For a release bundle in GitHub Code
> Scanning, run per-library `compare --format sarif` for the libraries you want
> to surface there.

---

## 5) CI recipes by platform

| CI need | Pattern |
|---|---|
| Fast PR gate for one library | Commit/download `abi-baseline.json`; run `compare` on each PR. |
| Release-quality baseline | Generate the baseline at release time and upload it as a release asset — see [Storing Baselines](baseline-storage.md). |
| GitHub-native | Use the [GitHub Action](github-action.md); upload SARIF for the Security tab and inline annotations. |
| GitLab / Jenkins / Azure | Emit `--format junit`; publish it to the native test dashboard (see [Output Formats → JUnit](output-formats.md#junit-xml-output)). |
| Raw shell CI (any system) | Drive the CLI directly; gate on the exit code. See [CLI Usage](cli-usage.md) and [Baseline Management](baseline-management.md). |
| Offline / air-gapped | Pre-dump snapshots, then `abicheck compare old.json new.json` — no castxml or network needed. |
| Multi-platform project | Matrix over Linux/macOS/Windows, emit JSON per platform, aggregate in a final gate job — see [GitHub Action](github-action.md). |
| Package / release validation | `compare` on RPM/Deb/tar/conda/wheel directory/package inputs, with debug/devel packages where available. |

---

## 5.5) How deep, how often — a three-tier cadence

§2's accuracy ladder and §3's failure policy are *what* to check; this is
*when* to spend on which depth. The L4/L5 cost cliff (see [Cost guide](scan-levels.md#cost-guide-rules-of-thumb))
means "always run the deepest check on every push" is rarely the right
default — match the depth to how often the job runs:

| Tier | When it runs | Depth | Why |
|---|---|---|---|
| **PR gate** | Every push/PR | `abicheck scan build/libfoo.so --depth source --since origin/main` (or omit `--depth` for risk-driven `auto` — see [`scan` § Let risk pick the depth](scan-levels.md#let-risk-pick-the-depth-auto-localdev-only)) | Diff-seeded `source` seeded by `--since`/`--changed-path` scopes the expensive L4 replay to just the touched TUs — an order of magnitude cheaper than an unseeded whole-library `source` scan for the same verdict on a real PR diff. `auto`'s risk scoring (`risk.py`) already encodes "escalate on public-header/export-map/ABI-flag touches, de-escalate on docs/tests" as the built-in ordering — you don't need to hand-write that policy. |
| **Nightly / scheduled** | Once a day, off the critical path | `abicheck scan build/libfoo.so --depth source` (unseeded — no `--since`/`--changed-path` — so it replays the whole library rather than a zero-TU no-op) | No diff seed to scope by, so this is the one place the L4 cost cliff is worth paying unconditionally — whole-library replay, compiler-matrix and stdlib-variant smoke, `compare --used-by` against key downstream consumers. Catches what a scoped PR gate structurally can't: breaks in files the PR didn't touch but whose *transitive* callers did. |
| **Release** | Once per release, amortized | `abicheck dump … --sources . -o release-baseline.abi.json`, then unseeded `--depth source` `scan`/`compare` against it | Produce once, reuse for every PR gate until the next release (see [Baseline Management](baseline-management.md)) — the amortized cost of a whole-library `source` scan is much lower spread across a release cycle than paid on every PR. |

Every tier's report ends with a coverage block stating what actually ran
(see [Reading the coverage block](scan-levels.md#reading-the-coverage-block))
and, per finding, an [`evidence_status`](output-formats.md#per-finding-epistemic-status-evidence_status)
label — so a cheap PR-gate run is never mistaken for a release-grade
guarantee: it states what it checked, not just what it found.

---

## Next steps by persona

- **Library maintainer** → [Getting Started](../getting-started.md),
  [Verdicts](../concepts/verdicts.md),
  [Policy Profiles](policies.md)
- **App developer** → [Application Compatibility](appcompat.md)
- **SDK / package maintainer** → [Multi-Binary Releases](multi-binary.md),
  [Baseline Management](baseline-management.md)
- **CI owner** → [GitHub Action](github-action.md),
  [Severity Configuration](severity.md), [Output Formats](output-formats.md)
- **Plugin author** → [Plugin Systems](plugin-systems.md)
- **Distro / package maintainer** → [Multi-Binary Releases](multi-binary.md),
  package mode in the [GitHub Action](github-action.md)
- **Migrating** → [from ABICC](from-abicc.md),
  [from libabigail](from-libabigail.md)
