---
name: debug-abi-failure
description: Diagnose a native program that fails at runtime in a development, test, or deployment environment because of a shared-library mismatch — "undefined symbol", "symbol lookup error", "version `X' not found", "cannot open shared object file", a crash or wrong results that started after a library was updated or rebuilt, a plugin that stops loading, or a C++ symbol containing `__cxx11`/`B5cxx11` that cannot be found. Use when something already built stopped working and the question is why and how to fix it, on Linux ELF with GCC or Clang. Finds which library copy the loader actually picked, compares it with the one the program was built against, names the root cause, and says what fixes it. Also says when the problem is not an ABI mismatch at all. Not for reviewing a proposed change before it ships — that is a compatibility review.
license: Apache-2.0
metadata:
  abicheck-version-range: ">=0.6.0,<0.7.0"
  layer: A
  source: skills-src/debug-abi-failure/SKILL.md
---

# Debugging a runtime ABI failure

Someone has a program that **already stopped working** — it will not start,
a plugin will not load, or it crashes or computes nonsense — and the timing
points at a shared library: it was updated, rebuilt, or moved. They are
asking **why is this happening, and what do I change to make it work?** The
answer is a diagnosis backed by evidence from the actual environment, ending
in the [report below](#the-diagnosis-you-report), not a list of commands
tried and not a guess from reading source.

Read [safety invariants](../shared/safety-invariants.md) before acting. Two
of them govern this workflow more than any other: never report a cause you
did not observe evidence for, and never change the environment (install,
delete, relink, edit a loader path) without being asked.

**This is not a compatibility review.** A compatibility review starts from
two versions of a library and asks whether a change is safe to ship. This
workflow starts from a symptom in a running environment and works backwards
to its cause. When the user is really asking "is this change safe?", a
compatibility review is the right tool, not this.

## Scope

Designed for: **Linux ELF; GCC and Clang; C and C++ shared libraries; a
program or plugin plus the library it was built against and the library it
actually loads.** macOS (`dyld`) and Windows (DLL) failures follow the same
reasoning, but the loader details differ and the evidence below is ELF's —
say so, and report the diagnosis as unverified where the ELF evidence does
not apply.

## Preflight

Run `abicheck --version` and check it against this skill's declared
version range (`metadata.abicheck-version-range` in this file's
frontmatter). Outside it, stop and say so. If abicheck is not installed, say so and how to install it; do not
install it yourself. You can still gather the loader evidence in step 2
without it, but report the diagnosis as unverified.

## Step 1 — Reproduce and capture the exact symptom

Run the failing program the way the user runs it (their launcher script,
environment variables, working directory) and copy the **exact** message.
Paraphrase loses the evidence: the symbol name, the version node, the
library path, and the requiring object are all in that one line.

Classify it; each family points to a different first question:

| Symptom | First question |
|---|---|
| `undefined symbol: NAME` / `symbol lookup error` | which library was loaded, and does it export `NAME`? |
| `version 'NODE' not found (required by ...)` | which library was loaded, and which version nodes does it define? |
| `cannot open shared object file` | is the needed SONAME present anywhere the loader searches? |
| a missing symbol containing `__cxx11` or `B5cxx11` | were the program and the library built with the same C++ standard-library ABI setting? |
| crash, corrupted data, or wrong results with no loader error | did a type's layout or a function's signature change between the build-time headers and the loaded library? |
| fails, but nothing about libraries changed | is this an ABI problem at all? |

## Step 2 — Find which library copy the loader actually used

The most common mistake is to reason about the library you *think* is
loaded. Ask the loader, under the user's real environment. See
[the diagnostics reference](references/diagnostics.md#which-library-actually-loads)
for the commands.

- Note every copy of the library on the search path, not just the first.
  A stale older copy earlier on `LD_LIBRARY_PATH` or in an `RPATH`/`RUNPATH`
  is its own root cause, different from "the library is broken": the
  correct copy exists and simply lost the search.
- Note what the program was **built against**: the SDK, the build tree, or
  the package headers and library it linked with at build time.

## Step 3 — Compare the built-against library with the loaded one

This is the deterministic core. Compare the library the program was built
against (old) with the library the loader actually used (new), with each
side's headers, per [the diagnostics reference](references/diagnostics.md#compare-built-against-with-loaded).
The findings name the mechanism: a removed export, a removed version node, a
changed type size or field offset, a changed signature.

Read the result the way [report interpretation](../shared/report-interpretation.md)
describes, comparability first. `NO_CHANGE` between the two, plus a loader
that resolved every symbol, is strong evidence that the problem is **not**
an ABI mismatch between these two libraries.

When the two copies come from the same source but different builds, check
the build flags too. `-D_GLIBCXX_USE_CXX11_ABI`, `-fvisibility`, `-std`
and packing flags change the ABI without changing a single source line; see
[compiler and build profiles](../shared/compiler-and-build-profiles.md).

## Step 4 — Name the root cause

Pick the one cause the evidence supports, from this closed set:

| Cause | Evidence that supports it |
|---|---|
| `symbol_removed` | the loaded library does not export a symbol the program needs, and it is the copy the program is meant to use |
| `symbol_version_missing` | the symbol exists, but the loaded library lacks the version node the program requires (a library, or a system runtime such as libstdc++, older than the one the program was built against) |
| `layout_changed` | no loader error, but a type's size or field offsets, or a function's signature, differ between the build-time headers and the loaded library |
| `stale_library_loaded` | a correct copy exists, but an older or different copy wins the search order |
| `cxx_abi_mismatch` | same source, but the program and library disagree on a C++ ABI build setting, most often `_GLIBCXX_USE_CXX11_ABI` |
| `not_an_abi_problem` | every symbol resolves and the built-against and loaded libraries are ABI-identical; the failure is elsewhere |

If two causes seem to apply, report the one closest to the failure and
mention the other. If the evidence does not support any of them, say that
the cause is unverified and what evidence is missing. Do not pick the
nearest-sounding one.

## Step 5 — Recommend the fix that matches the cause

Each cause has a different fix. Rebuilding everything happens to fix
several of them, but it is the wrong advice for `stale_library_loaded`,
where the right fix is a path, and for `not_an_abi_problem`, where nothing
about the libraries needs to change. See
[the causes and fixes reference](references/diagnostics.md#causes-and-fixes)
and the [remediation catalog](../shared/remediation-catalog.md) for the
library-side fixes.

Offer the fix; do not apply it unless asked. After any fix the user applies,
re-run the program and the comparison to confirm the symptom is gone.

## The diagnosis you report

End with this, not with a transcript:

| Field | Content |
|---|---|
| **Symptom** | the exact message or behaviour, and how it was reproduced |
| **Loaded vs built against** | the library path the loader used, the one the program was built against, and any other copies on the search path |
| **Evidence** | the loader resolution and the comparison you ran, and what each showed |
| **Root cause** | one of the step 4 causes, in one sentence of plain language |
| **Fix** | the change that addresses that cause, and the alternatives in order of preference |
| **Verify** | how the user confirms the fix: the command to re-run, and what they should see |
| **Unknowns** | anything you could not check, such as the production environment, `dlopen` loads you could not see, or behaviour changes a comparison cannot detect |

## Termination criteria

Done when the symptom has been reproduced, the loaded library identified,
the built-against and loaded libraries compared, one cause named with the
evidence behind it, and a fix recommended with a way to verify it. Or, when
any of those could not be done, when the report says which and why.
