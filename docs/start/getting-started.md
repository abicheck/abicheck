---
doc_type: hub
audience:
  - library-maintainer
level: beginner
lifecycle: active
generated: false
---

# Getting Started

**abicheck** compares two versions of a C/C++ shared library and tells you whether existing binaries will break. It supports ELF (Linux), PE/COFF (Windows), and Mach-O (macOS) binaries.

On all platforms it provides binary metadata analysis (exports, imports, dependencies) and header AST analysis (via castxml). Debug info cross-check uses DWARF on Linux (falling back to BTF/CTF when DWARF isn't present) and PDB on Windows; Mach-O has no debug-info cross-check today, so a headerless macOS input gets only binary metadata — header AST is available on any platform, Mach-O included, once you supply `-H` — see [Platform Support](../reference/platforms.md) for exactly what each platform sees without headers.

> **In CI already?** Skip straight to the [GitHub Action](../use/github-action.md)
> — it installs everything and runs the check in a few lines of YAML.

---

## What question are you asking?

abicheck ships several commands; pick the one that matches your question. If
you're unsure, start with `abicheck compare` — it's the default workflow.

| Your question | Command | See |
|---------------|---------|-----|
| **Did my library break?** — does upgrading it break existing consumers? | `abicheck compare` | [Run your first check](first-check.md) |
| **Does my application still work** with the new library version? | `abicheck compare --used-by` | [Application Compatibility](../use/appcompat.md) |
| **Did my whole package / release break?** | `abicheck compare` | [Multi-Binary Releases](../use/multi-binary.md) |
| **Gate a pull request** with the deepest evidence available (headers + build + sources)? | `abicheck scan` | [Source-Scan Depth](../use/scan-levels.md) |
| Will this binary load and resolve correctly in this sysroot — and does its dependency tree have unresolved symbols? | `abicheck deps tree` (`--sysroot /rootfs` for a specific root) | [CLI Usage](../use/cli-usage.md) |
| Did anything in the dependency stack change between two sysroots / images? | `abicheck deps compare --old-root … --new-root …` | [CLI Usage](../use/cli-usage.md) |
| I'm migrating from `abi-compliance-checker` and want the same flags. | `abicheck compat` | [Migrating from ABICC](../use/from-abicc.md) |
| Save a reusable ABI baseline for CI. | `abicheck dump` | [Creating and Comparing a Baseline](../use/create-baseline.md) |

For the full decision matrix — every artifact layout, accuracy tier, and CI
policy — see [**Choose Your Workflow**](choose-your-workflow.md).

---

## Your first five minutes

1. ➡️ **[Install abicheck](install.md)** — conda-forge (recommended, bundles
   `castxml`) or a lightweight `pip install`.
2. ➡️ **[Run your first check](first-check.md)** — compare two shared
   libraries from the repo's example catalog, then your own library.
3. ➡️ **[Understand your first report](first-report.md)** — output formats
   and what the exit code means for CI.
4. ➡️ **[Choose Your Workflow](choose-your-workflow.md)** — once the basic
   flow works, map your actual artifacts/CI policy to the exact command.

Two other common day-one workflows, covered on their own canonical pages
rather than here: saving a reusable baseline for CI
([Creating and Comparing a Baseline](../use/create-baseline.md)) and checking
whether an *application* (not just the library) still works after an update
([Application Compatibility](../use/appcompat.md)).

---

## Next steps

Jump straight to your persona:

- **Library maintainer** → [Verdicts](../learn/verdicts.md), [Policy Profiles](../use/policies.md)
- **App developer** → [Application Compatibility](../use/appcompat.md)
- **SDK / package maintainer** → [Multi-Binary Releases](../use/multi-binary.md), [Baseline Management](../use/baseline-management.md)
- **CI owner** → [GitHub Action](../use/github-action.md), [Severity Configuration](../use/severity.md), [Output Formats](../use/output-formats.md)
- **Plugin author** → [Plugin Systems](../use/plugin-systems.md)
- **Distro / package maintainer** → [Multi-Binary Releases](../use/multi-binary.md)
- **Migrating from ABICC / libabigail** → [from ABICC](../use/from-abicc.md), [from libabigail](../use/from-libabigail.md)

Background reading:

- [ABI/API Compatibility](../learn/abi-api-handling.md) — real-world ABI/API break scenarios and how to prevent them
- [Limitations](../learn/limitations.md) — what abicheck does *not* catch
