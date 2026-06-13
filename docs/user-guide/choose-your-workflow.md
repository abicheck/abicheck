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
| One shared library — does v2 break v1 consumers? | `abicheck compare libv1.so libv2.so` | `abicheck compare libv1.so libv2.so --old-header include/v1/ --new-header include/v2/` — the primary flow |
| Same public header for both versions | `abicheck compare libv1.so libv2.so -H include/foo.h` (`-H include/` scans a directory recursively) | When compiler flags affect the ABI, capture build context at dump time (`abicheck dump … -H include/foo.h -p build/`) and compare the snapshots |
| No headers at all | `abicheck compare libv1.so libv2.so` | Binary-only fallback is weaker (see [the input-quality ladder](#2-how-much-accuracy-do-you-need)); add debug info via `--debug-root1/2` |
| Stripped production binaries | `abicheck compare old.so new.so --debug-root1 old-debug --debug-root2 new-debug` (or `--debuginfod` to fetch by build-id) | Also pass public headers (`-H`) for highest confidence |
| A CI baseline vs a fresh build | `abicheck dump libfoo.so -H include/ -o baseline.json`, then `abicheck compare baseline.json build/libfoo.so --new-header include/` | Store baselines in GitHub Releases, the repo, the Actions cache, or artifact storage — see [Baseline Management](baseline-management.md) |
| Two snapshots (offline / air-gapped) | `abicheck compare old.json new.json` | No headers/castxml/network needed — everything is baked into the snapshots |
| Several DSOs shipped together | `abicheck compare-release release-1.0/ release-2.0/ -H include/` (per-library results on all platforms; the cross-library bundle/dependency-skew analysis is **Linux/ELF only**) | Add `--manifest` only for template instantiations, dlsym/plugin contracts, internal stable exports, or symbol-version promises |
| RPM / Deb / tar / conda / wheel packages | `abicheck compare-release old.rpm new.rpm` | Add `--debug-info1/2` (debuginfo packages) and `--devel-pkg1/2` (header/devel packages) where available |
| An application + a library upgrade | `abicheck appcompat ./myapp libfoo.so.1 libfoo.so.2` | Add `-H include/`; use `--check-against new.so` when no old library exists (symbol-availability only) |
| A host that `dlopen`s plugins | `abicheck plugin-check plugin.v1.so plugin.v2.so -r plugin_init` | Use `--host-contract host.syms --policy plugin_abi` |
| Will this binary load in this sysroot / rootfs? | `abicheck deps ./app --sysroot /rootfs` | `abicheck deps ./app` alone checks the dependency tree resolves |
| Two sysroots / container images to compare | `abicheck stack-check usr/bin/app --baseline /old-root --candidate /new-root` | Per-library ABI diff across the whole transitive dependency stack |
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
more when you need more confidence.

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
  `--debug-root1` / `--debug-root2`, or fetch them by build-id with
  `--debuginfod`. See [CLI Usage → Debug-info
  resolution](cli-usage.md#debug-artifact-resolution).
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
# (i.e. source/API breaks are allowed through)
abicheck compare old.json new.so \
  --new-header include/ \
  --severity-preset info-only \
  --severity-abi-breaking error

# Fail on binary ABI breaks AND new public API additions
abicheck compare old.json new.so \
  --new-header include/ \
  --severity-addition error
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

> **`compare-release` formats are narrower:** the bundle/package command emits
> only `markdown`, `json`, and `junit` — **not** `sarif` or `html`. Those two
> formats apply to single-library `compare`. For a release bundle in GitHub Code
> Scanning, run per-library `compare --format sarif` for the libraries you want
> to surface there.

---

## 5) CI recipes by platform

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
