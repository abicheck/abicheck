---
doc_type: reference
level: advanced
lifecycle: active
summarizes:
  - config-keys
---

# Compiler and build profiles

The same source produces a different ABI under a different toolchain. A
compatibility comparison is only meaningful when both sides were extracted
under the same profile, or when the difference between profiles *is* the
question being asked.

## What makes up a profile

The fields abicheck records in a snapshot's extraction contract, any of which
can make a pair non-comparable:

- compiler family and version
- ABI dialect and language standard (`-std=`)
- target triple, pointer width, endianness
- macro definitions and other ABI-relevant pass-through flags
- include order / header sequence

The scope half of the contract records which headers, public header
directories, and translation units were in view.

## The dials

| Dial | Use |
|---|---|
| `--lang c\|c++` | language of the surface |
| `--gcc-path`, `--gcc-prefix` | select the compiler driver used for header extraction |
| `--compiler-option` (repeatable) | ABI-relevant flags (`-std=`, `-D`, `-fvisibility=`, ...) |
| `--sysroot`, `--include` / `-I`, `--nostdinc` | header search context |
| `--ast-frontend` | which header-AST backend parses the headers |
| `--env-matrix` | compare across several environments in one run |
| `--profile ci-gate\|release-cut\|quick` | a named run profile |

Project-level defaults belong in `.abicheck.yml` rather than repeated on the
command line; the exhaustive key reference is
[the config file page](../../docs/reference/config-file.md), and
`abicheck project validate` checks a project's own configuration.

## Two distinct failure modes, do not conflate them

**Profile mismatch between the two sides.** The comparison is refused —
exit `16`, `verdict: null`, `reason.kind == "profile_mismatch"`. Remediation
is to re-extract one side under the other's profile. See
[baseline-and-comparability.md](baseline-and-comparability.md).

**Profile change as the change under review.** The user upgraded their
compiler or standard library and wants to know what it cost. Here the
mismatch is the subject rather than an obstacle — but the gate does not know
that, so the ordinary comparison is still refused (exit `16`, `verdict:
null`). You must opt in explicitly:

```bash
abicheck compare OLD NEW --diagnostic-comparison --format json
```

That is ADR-050's sanctioned escape hatch: it downgrades the hard failure to
a **tentative** diff and stamps `assurance: "none"` throughout the report.
Two obligations come with it, and neither is optional:

- **Report the tentative status.** Say the run was diagnostic, carry
  `assurance: "none"` and the comparability warning into your summary, and
  never present the result as a compatibility verdict. A diagnostic run may
  not back a release decision
  ([safety-invariants.md](safety-invariants.md) item 3).
- **Do not use it to get past a mismatch you did not intend.** If the profile
  difference is accidental, the fix is to re-extract under one profile, not
  to force the diff.

Expect large, mechanical finding sets dominated by mangling and template
instantiation churn; group them
([root-cause-grouping.md](root-cause-grouping.md)) before summarizing, and
scope to the library's own surface
([public-surface-and-scoping.md](public-surface-and-scoping.md)) so
standard-library churn does not drown the library's own delta.

## Practical rules

- Pin the profile in CI. An unpinned toolchain turns every runner upgrade
  into a spurious compatibility incident.
- When you cannot reproduce the baseline's profile, say the comparison is
  profile-limited rather than reporting a clean verdict.
- `contract_coverage == "partial"` means only one side carried a fingerprint.
  That is a real gap in the guarantee, not a formality.
