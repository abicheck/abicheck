---
name: explain-abi-change
description: Explain an ABI-related observation in a C/C++ development environment and say what actually changed — a program or plugin that stopped loading ("undefined symbol", "symbol lookup error", "version `X' not found", "cannot open shared object file"), a crash or wrong results after a library was updated or rebuilt, a C++ symbol containing `__cxx11`/`B5cxx11` that cannot be found, or a shared-library update whose new or different symbols a developer wants to understand ("the library was bumped — what changed, and does our program care?"). Use when a developer is trying to understand what is going on between a program and the shared libraries it uses, on Linux ELF with GCC or Clang. Finds which library copy is actually used, compares it with the one the program was built against, names the mechanism behind the change, and says what (if anything) to do. Also says when the change is harmless or not ABI-related at all. Not for reviewing a proposed change before it ships — that is a compatibility review.
license: Apache-2.0
metadata:
  abicheck-version-range: ">=0.6.0,<0.7.0"
  layer: A
  source: skills-src/explain-abi-change/SKILL.md
---

# Explaining an ABI change

A developer has noticed something between a program and the shared libraries
it uses and wants to understand it. Sometimes it is a failure — the program
will not start, a plugin will not load, it crashes or computes nonsense.
Sometimes nothing is failing yet: a library was updated or rebuilt, its
symbols look different, and the developer wants to know what changed and
whether it matters. Either way the question is **what is this change, what
mechanism is behind it, and what (if anything) should I do?** The answer is
an explanation backed by evidence from the actual environment, ending in the
[report below](#the-explanation-you-report), not a list of commands tried
and not a guess from reading source.

Read [safety invariants](../shared/safety-invariants.md) before acting. Two
of them govern this workflow more than any other: never report a mechanism
you did not observe evidence for, and never change the environment (install,
delete, relink, edit a loader path) without being asked.

**This is not a compatibility review.** A compatibility review starts from a
proposed change and asks whether it is safe to ship. This workflow starts
from what the developer observed in an existing environment and works out
what changed and why it behaves the way it does. When the user is really
asking "is this change safe to release?", a compatibility review is the
right tool, not this.

## Scope

Designed for: **Linux ELF; GCC and Clang; C and C++ shared libraries; a
program or plugin plus the library it was built against and the library it
actually uses.** macOS (`dyld`) and Windows (DLL) follow the same reasoning,
but the loader details differ and the evidence below is ELF's — say so, and
report the explanation as unverified where the ELF evidence does not apply.

## Preflight

Run `abicheck --version` and check it against this skill's declared
version range (`metadata.abicheck-version-range` in this file's
frontmatter). Outside it, stop and say so. If abicheck is not installed, say so and how to install it; do not
install it yourself. You can still gather the loader evidence in step 2
without it, but report the explanation as unverified.

## Step 1 — Pin down the observation

Reproduce what the developer saw the way they see it (their launcher script,
environment variables, working directory) and copy the **exact** output.
Paraphrase loses the evidence: the symbol name, the version node, the
library path, and the requiring object are all in that one line. When
nothing fails, the observation is the difference itself — which library
changed, from which build to which.

Each kind of observation points to a different first question:

| Observation | First question |
|---|---|
| `undefined symbol: NAME` / `symbol lookup error` | which library was loaded, and does it export `NAME`? was it removed, or is the loaded library simply older than the build? |
| `version 'NODE' not found (required by ...)` | which library was loaded, and which version nodes does it define? |
| `cannot open shared object file` | is the needed SONAME present anywhere the loader searches? |
| a missing symbol containing `__cxx11` or `B5cxx11` | were the program and the library built with the same C++ standard-library ABI setting? |
| crash, corrupted data, or wrong results with no loader error | did a type's layout or a function's signature change between the build-time headers and the loaded library? |
| the library changed, nothing fails, "what changed?" | what does the comparison show, and does any of it touch what the program uses? |
| fails, but nothing about libraries changed | is this an ABI question at all? |

## Step 2 — Find which library copy is actually used

The most common mistake is to reason about the library you *think* is
loaded. Ask the loader, under the user's real environment. See
[the investigation reference](references/diagnostics.md#which-library-actually-loads)
for the commands.

- Note every copy of the library on the search path, not just the first.
  A stale older copy earlier on `LD_LIBRARY_PATH` or in an `RPATH`/`RUNPATH`
  is its own explanation, different from "the library changed": the
  intended copy exists and simply lost the search.
- Note what the program was **built against**: the SDK, the build tree, or
  the package headers and library it linked with at build time.

## Step 3 — Compare the built-against library with the used one

This is the deterministic core. Compare the library the program was built
against (old) with the library it actually uses (new), with each side's
headers, per [the investigation reference](references/diagnostics.md#compare-built-against-with-loaded).
The findings name the change: an added or removed export, a removed version
node, a changed type size or field offset, a changed signature.

Read the result the way [report interpretation](../shared/report-interpretation.md)
describes, comparability first. `NO_CHANGE`, or only additions, plus a loader
that resolved every symbol, is strong evidence that nothing between these
two libraries breaks the program.

When every "removed" symbol is one the used library never had yet — the
used copy is an *older* release than the build's — compare in the other
direction too. If the used-to-built-against comparison shows only additions,
the library did not remove anything; the program was built against a newer
release than the one it runs on. The reverse comparison is evidence for the
mechanism only: the change you report is still the one from the
built-against library to the used one, which removes what the program needs.

When the two copies come from the same source but different builds, check
the build flags too. `-D_GLIBCXX_USE_CXX11_ABI`, `-fvisibility`, `-std`
and packing flags change the ABI without changing a single source line; see
[compiler and build profiles](../shared/compiler-and-build-profiles.md).

## Step 4 — Name the mechanism

Pick the one mechanism the evidence supports, from this closed set:

| Mechanism | Evidence that supports it |
|---|---|
| `symbol_removed` | the used library does not export a symbol the program needs, it is the copy the program is meant to use, and it is not older than the build's |
| `library_older_than_build` | the used library lacks symbols the program needs because it is an older release than the one the program was built against; compared the other way round, the build's library only adds to it |
| `symbol_version_missing` | the symbol exists, but the used library lacks the version node the program requires (a library, or a system runtime such as libstdc++, older than the one the program was built against) |
| `layout_changed` | a type's size or field offsets, or a function's signature, differ between the build-time headers and the used library |
| `stale_library_loaded` | a correct copy exists, but an older or different copy wins the search order |
| `cxx_abi_mismatch` | same source, but the program and library disagree on a C++ ABI build setting, most often `_GLIBCXX_USE_CXX11_ABI` |
| `not_an_abi_problem` | every symbol resolves and the built-against and used libraries are ABI-identical or differ only by additions; nothing about the libraries breaks the program |

If two mechanisms seem to apply, report the one closest to what the
developer observed and mention the other. If the evidence does not support
any of them, say that the mechanism is unverified and what evidence is
missing. Do not pick the nearest-sounding one.

## Step 5 — Say what to do about it

Each mechanism has a different answer. Rebuilding everything happens to fix
several of them, but it is the wrong advice for `stale_library_loaded`,
where the right fix is a path, for `library_older_than_build`, where the
right fix is deploying the newer library (or building against the oldest one
you must support), and for `not_an_abi_problem`, where nothing about the
libraries needs to change. See
[the mechanisms and fixes reference](references/diagnostics.md#causes-and-fixes)
and the [remediation catalog](../shared/remediation-catalog.md) for the
library-side fixes.

Offer the fix; do not apply it unless asked. After any fix the user applies,
re-run the program and the comparison to confirm the result.

## The explanation you report

End with this, not with a transcript:

| Field | Content |
|---|---|
| **Observation** | the exact message, behaviour, or library difference, and how it was reproduced |
| **Used vs built against** | the library path actually used, the one the program was built against, and any other copies on the search path |
| **Evidence** | the loader resolution and the comparison you ran, and what each showed |
| **What changed** | one of the step 4 mechanisms, in one sentence of plain language |
| **What to do** | the change that addresses it (or "nothing"), and the alternatives in order of preference |
| **Verify** | how the user confirms it: the command to re-run, and what they should see |
| **Unknowns** | anything you could not check, such as the production environment, `dlopen` loads you could not see, or behaviour changes a comparison cannot detect |

## Termination criteria

Done when the observation has been reproduced, the used library identified,
the built-against and used libraries compared, one mechanism named with the
evidence behind it, and an action (or "nothing to do") recommended with a way
to verify it. Or, when any of those could not be done, when the report says
which and why.
