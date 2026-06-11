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

## 1) The workflow chooser

| Your situation | Recommended workflow | Minimum command | Stronger / production command |
|---|---|---|---|
| I maintain one shared library and want to know if v2 breaks v1 consumers | Single-library ABI compare | `abicheck compare libv1.so libv2.so` | `abicheck compare libv1.so libv2.so --old-header include/v1/ --new-header include/v2/` |
| Headers are unchanged between releases | Same-header compare | `abicheck compare libv1.so libv2.so -H include/foo.h` | When compiler flags affect the ABI, capture build context at dump time (`abicheck dump … -H include/foo.h -p build/`) and compare the snapshots |
| I have no headers | Binary-only quick check | `abicheck compare libv1.so libv2.so` | Provide debug info via `--debug-root1/2` — this is weaker (see [the input-quality ladder](#2-how-much-accuracy-do-you-need)) |
| I have stripped production binaries | Debug-assisted compare | `abicheck compare old.so new.so --debug-root1 old-debug --debug-root2 new-debug` | Also pass public headers (`-H`) for highest confidence |
| I want a CI baseline | Snapshot workflow | `abicheck dump libfoo.so -H include/ -o baseline.json` then `abicheck compare baseline.json build/libfoo.so --new-header include/` | Store baselines in GitHub Releases, the repo, the Actions cache, or artifact storage — see [Baseline Management](baseline-management.md) |
| I ship several DSOs together | Bundle / release compare | `abicheck compare-release release-1.0/ release-2.0/ -H include/` | Add `--manifest` only for template instantiations, dlsym/plugin contracts, internal stable exports, or symbol-version promises |
| I ship RPM/Deb/tar/conda/wheel packages | Package compare | `abicheck compare-release old.rpm new.rpm` | Add `--debug-info1/2` (debuginfo packages) and `--devel-pkg1/2` (header/devel packages) where available |
| I am an application developer | App compatibility | `abicheck appcompat ./myapp libfoo.so.1 libfoo.so.2` | Add `-H include/`; use `--check-against` when no old library exists |
| I have a host/plugin system | Plugin contract check | `abicheck plugin-check plugin.v1.so plugin.v2.so -r plugin_init` | Use `--host-contract host.syms --policy plugin_abi` |
| I only want to fail CI on binary ABI breaks | Breakage-only gate | `abicheck compare old.json new.so --severity-preset info-only --severity-abi-breaking error` | In the GitHub Action, use `fail-on-breaking: true`, `fail-on-api-break: false` |
| I want visibility into new ABI additions | Addition reporting | `abicheck compare old.so new.so -H include/` | Additions show in the default report (any format); add `--severity-addition error` to also fail CI on them |
| I need human reports | Markdown / HTML | `--format markdown` or `--format html` | Add `--report-mode leaf --show-impact` for large diffs |
| I need machine / CI reports | JSON / SARIF / JUnit | `--format json`, `--format sarif`, or `--format junit` | SARIF for GitHub Code Scanning; JUnit for GitLab / Jenkins / Azure dashboards |
| I only have a static archive (`.a` / `.lib`) | Not supported directly | — | Extract members (`ar x libfoo.a`) and compare the resulting `.o` objects, or compare a shared library built from the same sources — see [Limitations](../concepts/limitations.md#static-import-library-archives-a-lib) |
| I want a dependency / sysroot check | Stack validation | `abicheck deps ./app --sysroot /rootfs` (resolve one env) / `abicheck stack-check usr/bin/app --baseline /old-root --candidate /new-root` (compare two envs) | See [CLI Usage](cli-usage.md) |

The rest of this page expands the four decisions packed into that table:
**what** you are comparing, **how much accuracy** you need, **how CI should
behave**, and **which report** to produce.

---

## 2) How much accuracy do you need?

The single biggest lever on what abicheck can *prove* is the quality of the
inputs you give it — its five additive evidence layers, **L0–L4**. More
evidence catches more breaks. Start at the layer your artifacts allow, and add
more when you need more confidence.

| Layer | Inputs | Confidence | What it newly catches |
|:--:|---|---|---|
| **L0** | Binaries only | **Low** | Symbol add/remove, SONAME/version changes, basic metadata |
| **L1** | + debug info | **Medium** | Struct layout, field offsets, enum values, calling convention, emitted-ABI type changes |
| **L2** | + headers | **High** | Declared public API surface, source-level API breaks, inline/template-related surface |
| **L3** | + build flags (`-p build/`) | **Higher** | The exact ABI-affecting flags the library was built with (`-std`, `_GLIBCXX_USE_CXX11_ABI`, `-fvisibility`, …) |
| **L4** | + sources (evidence pack) | **Best** | Facts that never reach the binary: macro/`constexpr` values, default-argument values, uninstantiated templates |

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
  `--debug-root1` / `--debug-root2`, or fetch them by build-id with
  `--debuginfod`. See [CLI Usage → Debug-info
  resolution](cli-usage.md#debug-artifact-resolution).
- **Compiler flags affect the ABI** (e.g. `-D` macros that change struct
  layout)? Capture the build context at **dump** time with
  `abicheck dump … -p build/` / `--compile-db` so the header AST is parsed the
  way it was actually compiled, then compare the resulting snapshots. (These
  build-context flags live on `dump`, not `compare`.)

---

## 3) Comparison variants — what are you comparing?

abicheck has one command per artifact topology. Pick by what you physically
have on disk.

| You have… | Command | Notes |
|---|---|---|
| Two `.so` / `.dll` / `.dylib`, each with its own header | `abicheck compare old new --old-header … --new-header …` | The primary flow. |
| Two binaries, **same** public header | `abicheck compare old new -H include/foo.h` | `-H` applies to both sides. |
| A header **directory** (not one file) | `abicheck compare old new -H include/` | Recursive scan for `*.h`, `*.hpp`, … |
| No headers at all | `abicheck compare old new` | Binary-only fallback; weaker (may miss type/signature breaks). |
| Stripped binaries + separate debug files | `abicheck compare old.so new.so --debug-root1 … --debug-root2 …` | Or `--debuginfod` to fetch by build-id. |
| A saved baseline vs a fresh build | `abicheck dump … -o baseline.json` then `abicheck compare baseline.json build/libfoo.so --new-header …` | The default CI baseline path. |
| Two snapshots (offline / air-gapped) | `abicheck compare old.json new.json` | No headers/castxml needed — already baked in. |
| Several `.so` files shipped together | `abicheck compare-release old/ new/ -H include/` | Catches cross-library breaks per-library compare misses. **Linux/ELF only.** |
| RPM / Deb / tar / conda / wheel packages | `abicheck compare-release old.rpm new.rpm` | Add `--debug-info1/2` and `--devel-pkg1/2` for full type-level analysis. |
| An application + a library upgrade | `abicheck appcompat ./myapp old new -H include/` | Filters the diff to changes that affect *your* app. |
| An app, no old library yet | `abicheck appcompat ./myapp --check-against new.so` | Weak mode: symbol-availability only. |
| A host that `dlopen`s plugins | `abicheck plugin-check plugin.v1 plugin.v2 -r plugin_init` | Checks the host's required entrypoints. |
| A sysroot / container rootfs | `abicheck deps ./app --sysroot /rootfs` | Will the binary load and resolve in this environment? |
| Two sysroots / container images to compare | `abicheck stack-check usr/bin/app --baseline /old-root --candidate /new-root` | Per-library ABI diff across the whole transitive dependency stack. |
| A dependency tree | `abicheck deps ./app` | Does it resolve without unresolved symbols? |
| Only a static `.a` / `.lib` archive | *(unsupported directly)* | Extract members and compare `.o` objects, or build a shared library from the same sources — see [Limitations](../concepts/limitations.md#static-import-library-archives-a-lib). |

`compare` auto-detects each input: `.so` files are dumped on the fly, `.json`
snapshots are loaded directly — you can mix them freely. Deeper references:
[CLI Usage](cli-usage.md), [Tool Modes](tool-modes.md),
[Multi-Binary Releases](multi-binary.md),
[Application Compatibility](appcompat.md), [Plugin Systems](plugin-systems.md).

---

## 4) How should CI behave? — policy recipes

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

> **GitHub Action note:** the `severity-preset` / `severity-addition` inputs are
> wired into **`compare` mode only**. The Action's `compare-release` branch does
> **not** interpret the CLI's severity-aware exit codes — it only recognizes the
> verdict codes (`0/2/4/8`) and treats anything else (including the severity exit
> code `1`) as a tool error. So gate a release/bundle in the Action with
> `fail-on-breaking` / `fail-on-api-break` (verdict-based; these apply to both
> `compare` and `compare-release`). To gate `compare-release` on **severity**
> (e.g. fail on additions), run the CLI directly in a shell step — where the
> severity exit code is honored — rather than through the Action wrapper.

```bash
# Report everything, fail ONLY on binary ABI breaks
abicheck compare old.json new.so \
  --new-header include/ \
  --severity-preset info-only \
  --severity-abi-breaking error

# Fail on binary ABI breaks AND new public API additions
abicheck compare old.json new.so \
  --new-header include/ \
  --severity-addition error

# Allow source/API breaks but block binary ABI breaks
abicheck compare old.json new.so \
  --new-header include/ \
  --severity-preset info-only \
  --severity-abi-breaking error
```

### Display filter (does **not** change verdict or exit code)

```bash
# Show only additions in a review report — verdict and exit code unchanged
abicheck compare old.json new.so \
  --new-header include/ \
  --show-only compatible,added
```

Full reference: [Severity Configuration](severity.md). The default model is
already "report additions but don't fail on them" — additions are classified in
the `addition` category, which defaults to `info`.

---

## 5) Which report? — output by audience

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

> **`compare-release` formats are narrower:** the bundle/package command emits
> only `markdown`, `json`, and `junit` — **not** `sarif` or `html`. Those two
> formats apply to single-library `compare`. For a release bundle in GitHub Code
> Scanning, run per-library `compare --format sarif` for the libraries you want
> to surface there.

---

## 6) CI recipes by platform

| CI need | Pattern |
|---|---|
| Fast PR gate for one library | Commit/download `abi-baseline.json`; run `compare` on each PR. |
| Release-quality baseline | Generate the baseline at release time and upload it as a release asset — see [Baseline Management](baseline-management.md). |
| GitHub-native | Use the [GitHub Action](github-action.md); upload SARIF for the Security tab and inline annotations. |
| GitLab / Jenkins / Azure | Emit `--format junit`; publish it to the native test dashboard (see [Output Formats → JUnit](output-formats.md#junit-xml-output)). |
| Raw shell CI (any system) | Drive the CLI directly; gate on the exit code. See [Local Compare](local-compare.md). |
| Offline / air-gapped | Pre-dump snapshots, then `abicheck compare old.json new.json` — no castxml or network needed. |
| Multi-platform project | Matrix over Linux/macOS/Windows, emit JSON per platform, aggregate in a final gate job — see [GitHub Action](github-action.md). |
| Package / release validation | `compare-release` on RPM/Deb/tar/conda/wheel inputs, with debug/devel packages where available. |

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
