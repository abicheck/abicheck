---
doc_type: explanation
audience:
  - library-maintainer
  - ci-owner
level: intermediate
canonical_for:
  - finding-triage
summarizes:
  - evidence-model
depends_on:
  - abicheck/comparability.py
  - abicheck/elf_symbol_filter.py
  - abicheck/buildsource/crosscheck.py
lifecycle: active
generated: false
---

# Triage a Suspicious Finding

A finding that looks wrong has three possible endings: it is a real break,
the comparison was given the wrong inputs, or it is a documented limitation
of what static comparison can see. The six questions below are in the order
that reaches an answer fastest — each one either ends the triage or hands
you to the next, and each names the flag that re-runs the comparison to
confirm. Do not skip ahead to a suppression: a suppression written for a
wrong-input finding hides the next real one.

### 1. Did both sides get the same kind of evidence?

A comparison is only as good as the *weaker* side. A stripped old binary
against a new one built with `-g` reports every struct as newly visible; a
new side with headers against an old side without reports the whole public
API as added; a source-depth scan against a binary-only baseline reports
every macro and inline function as new. The report says what each side had
— the evidence tier and per-side assurance in the summary
([Output Formats § Analysis confidence and evidence tier](../use/output-formats.md#analysis-confidence-and-evidence-tier))
— so read that block before the findings. The levels themselves are
defined in [Evidence & Detectability](evidence-and-detectability.md).

Even the sides up, then re-run. Separate debug files attach per side; the
option is repeatable and one token scopes one side:

```bash
abicheck compare old/libfoo.so new/libfoo.so -H include/ \
  --debug-root old=old/debug --debug-root new=new/debug
```

`--debuginfod` fetches by build-id when a server has them. If the finding
survives with equal evidence, go to 2.

### 2. Were the headers the binary's headers?

The most common wrong-input finding is a header/binary mismatch: the
headers passed with `-H` are not the ones the analysed binary was built
from, or they were parsed with different `-D` macros or include paths than
the build used, so the declared API and the compiled ABI disagree with
each other rather than with the previous release. The checklist is
[Troubleshooting § Check header/binary mismatch first](../use/troubleshooting.md#check-headerbinary-mismatch-first);
why the compile context matters this much is
[Detecting Breaks § 1a](abi-series/08-detection.md#1a-the-hidden-prerequisite-of-headerast-diffing-the-compile-context).
With build evidence on hand the checker reports the mismatch itself as a
finding rather than leaving you to infer it
([case148](../reference/examples/case148_xcheck_header_build_mismatch.md)),
and it can also see the case where two translation units compiled the
same type differently
([case149](../reference/examples/case149_xcheck_odr_variant.md)) — a real
problem in the build, not in the comparison. Fix the inputs, re-run; if it
survives, go to 3.

### 3. Were the two builds comparable at all?

Two binaries built under different profiles — a different optimisation
level, sanitizer, standard-library mode, or scope setting on one side —
differ in ways that are not contract changes. The comparability gate sits
in front of the verdict: an incomparable pair produces no verdict at all
(`compare` exits 16; `scan --against` exits 6), with a `reason` object
naming the fingerprint that differed. That is not a finding to triage;
rebuild one side under the other's profile. If you need to look anyway:

```bash
abicheck compare old.json new.json --diagnostic-comparison
```

This downgrades the refusal to a *tentative* diff stamped with no
assurance, so nothing downstream mistakes it for a trusted result. What is
fingerprinted, and which differences are carved out as legitimate, is
owned by [Build Profile Comparability](build-profile-comparability.md).
A pair that was comparable, and whose finding survives, goes to 4.

### 4. Is this a symbols-only false positive?

A comparison with no headers and no debug information sees only the
export table, and an export table contains things that are not API:
compiler-emitted helpers, template instantiations the consumer would
re-emit, standard-library internals with vague linkage. abicheck filters
the recognisable ones by name heuristics, and says so when the filter
applied, but a heuristic has edges
([ELF-Only Mode and Symbol Filtering](elf-symbol-filtering.md)). The fix
is headers, not a suppression: with `-H` the public surface is declared
rather than guessed, and the export-vs-declaration mismatch itself becomes
a pair of quality findings — exported but never declared public, declared
public but never exported
([case150](../reference/examples/case150_xcheck_export_public_pair.md)).
If the finding survives with headers, go to 5.

### 5. Is this a known limitation?

Some findings are wrong *because the evidence cannot decide them*, and the
honest answer is to know which. An uninstantiated template, an inline
function body, a macro: no binary carries these, so a binary-and-headers
comparison can only report the declaration, never the behaviour, and a
change the checker reports there may be a change in something it cannot
fully see. [Limitations & Known Boundaries](limitations.md) lists them;
where the source tier has an answer — templates, inline functions and
macros are exactly what L4 replays — re-run at that depth
([Source-Scan Depth](../use/scan-levels.md)) before concluding. A finding
that is neither a limitation nor answered by more evidence goes to 6.

### 6. Then it is real

The finding's own fields say how sure the checker is and what it rests on.
`evidence_status` states whether the change was observed or inferred
([§ Per-finding epistemic status](../use/output-formats.md#per-finding-epistemic-status-evidence_status));
`compatibility_decision` states whether policy scored it and under which
contract domain, and is `null` for a finding outside the selected contract
([§ Contract-evaluation report fields](../use/output-formats.md#contract-evaluation-report-fields-contract_relevance-contract_coverage_failures));
`reachability_state` says whether a path to the public surface was proven,
disproven, or never examined
([Source Graph Schema § reachability_state](../reference/source-graph-schema.md#reachability_state));
and under `--used-by` the report carries the consumer's actual call chain
to the changed entity ([Application Compatibility](../use/appcompat.md)),
which is the difference between "public in principle" and "your customer
calls it". `recommended_action` is the checker's own suggestion
([§ Recommended action per finding](../use/output-formats.md#recommended-action-per-finding-recommended_action)).

What to do about a real break: [Part 7 — Designing for
Stability](abi-series/07-designing-for-stability.md) holds the patterns,
and [Rollout and Governance](rollout-and-governance.md) says how to ship an
intentional one without hiding it. The next step of the series, *At Scale*,
asks the same questions of a release of several binaries.

---

**Ladder:** ← [Rollout and Governance](rollout-and-governance.md) · Step 7 · In Practice · [Products, Not Libraries](products-not-libraries.md) →
