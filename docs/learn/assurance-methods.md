---
doc_type: explanation
audience:
  - library-maintainer
  - ci-owner
level: intermediate
summarizes:
  - behavioral-compatibility
  - data-wire-compatibility
  - ownership-and-lifetime
  - concurrency-and-initialization
  - evidence-model
lifecycle: active
generated: false
---

# Assurance Beyond Static Checking: What Each Verification Method Actually Proves

> **Series navigation:** [Detecting Breaks](abi-series/08-detection.md) covers
> *why* one static method is never enough and what evidence each break family
> needs. This page picks up exactly where that one stops: **static artifact
> comparison — abicheck included — cannot prove behaviour.** So what do you
> run instead, or alongside it, for the parts it structurally cannot reach?

[Evidence & Detectability §5](evidence-and-detectability.md#5-what-abi-tools-cannot-prove)
and [Limitations](limitations.md) both name the boundary: macro-only changes,
inline/template bodies, `constexpr` semantics, and — the big one — anything
that is *behavioral* rather than structural (same signature and layout,
different meaning) are out of scope for any artifact diff. That boundary is
not a gap to be embarrassed about; it is a reason to run a **second kind of
check**, deliberately chosen for what it *can* prove, next to static checking
rather than instead of it.

## The assurance methods, and what each one actually proves

No single method below is a superset of the others — each observes a
genuinely different thing, the same way [the evidence layers](evidence-and-detectability.md#0-the-five-sources-of-information)
do for static checking. Pick the ones that match the promises your release
actually makes (see [Part 0 §2](abi-series/00-product-contract.md#2-compatibility-is-not-one-question-name-which-kind-you-mean)
for naming which kind of compatibility you're promising in the first place).

| Method | What it proves | What it does *not* prove |
|---|---|---|
| **Static artifact/header/source diff** (abicheck itself, across whichever evidence tiers you give it) | The shipped ABI facts, the declared source API, and — only with build/source evidence attached (L3/L4) — the source-only facts (macro values, inline/template bodies) that a header-only run cannot see; everything [Detecting Breaks](abi-series/08-detection.md) covers, gated by which tier you fed it | Anything about how a consumer actually uses the library, or what the code *does* at runtime |
| **Consumer rebuild test** | Source compatibility for the specific consumer(s) rebuilt — their code still compiles and links against the new headers | Binary compatibility for a consumer that doesn't rebuild; code paths the consumer's own build doesn't exercise |
| **Binary-swap test** (an old, already-built consumer run against the new library) | Backward *binary* compatibility for that consumer's actual, exercised usage — the promise a prebuilt-consumer release most needs to keep | Code paths the consumer binary doesn't call; says nothing about a *different* consumer's usage |
| **Reverse swap** (a new consumer built and run against the *old* library) | Forward/downgrade compatibility — relevant when an older library build is still shipped or pinned longer than the newest consumer | Same exercised-paths limit as binary-swap, in the other direction |
| **Host × plugin version matrix** | The two-sided contract a plugin/host relationship depends on, across the version combinations you actually support ([Plugin Systems](../use/plugin-systems.md), [Consumer Models](consumer-models.md)) | Any combination outside the matrix — an unlisted host/plugin pair is unverified, not verified-compatible |
| **Oldest-supported-OS/runtime load test** | The deployment floor: does the binary even load and link on the oldest target you claim to support ([Dependency & Runtime Floors](dependency-floors.md)) | Correctness beyond "it loaded" — a clean load is necessary, not sufficient |
| **Golden / differential output tests** | General [output/semantic behavioral compatibility](behavioral-compatibility.md) for the specific scenarios the fixtures cover — the one method here aimed at behaviour broadly, rather than one specific contract (lifetime, concurrency, wire format) the way the rows below it are | Any scenario the fixture set doesn't exercise; a passing suite is a statement about coverage, not universal correctness |
| **Old-reader/new-writer and new-reader/old-writer fixture tests** | Wire, storage, and serialization-format compatibility — the [data/wire dimension](data-wire-compatibility.md) static ABI/API checking cannot see at all | Schema paths or field combinations the fixtures don't exercise |
| **ASan-instrumented lifecycle tests** | [Ownership/lifetime contract](ownership-and-lifetime.md) violations across the library boundary — double-free, use-after-free, cross-allocator frees | Only what the exercised lifecycle actually triggers; an untested destruction order stays unverified |
| **TSan / stress / reentrancy tests** | [Concurrency and initialization contract](concurrency-and-initialization.md) violations — data races, unsafe reentrancy, ordering assumptions | Requires a workload that actually contends; a single-threaded run through the same suite proves nothing about the concurrency contract |

## Reading the table as a decision, not a checklist

Three practical rules follow directly from "each method proves something
different":

1. **Match the method to the promise, not the other way around.** A release
   that only promises source compatibility (consumers always rebuild) gets
   most of its value from consumer rebuild tests plus static header diffing;
   binary-swap testing is spending effort on a promise nobody made. A release
   shipping prebuilt binaries to consumers that don't rebuild needs the
   binary-swap test to be the release gate, not an afterthought — that is
   exactly the promise static ABI checking alone cannot fully stand in for,
   since it proves the *binary contract* held, not that the specific,
   already-compiled consumer you ship to still works.
2. **A method's "what it does not prove" column is not a defect in the
   method — it's the reason the *other* rows exist.** No single row is meant
   to close every gap; the set is meant to be composed. A CI pipeline that
   runs static checking plus one behavioral method (golden tests) plus one
   contract-specific method (ASan or TSan, chosen by what the change touches)
   covers structurally different failure classes with each addition, not
   diminishing returns on the same one.
3. **None of this replaces static checking — it's additive.** abicheck
   itself is the only method in this table that is *exhaustive over the
   evidence it was given* — every declaration and symbol in the captured
   snapshot is compared, without needing a hand-written test to exercise the
   right path first. That's narrower than "exhaustive over the shipped
   ABI/API surface": a run limited to artifact/header evidence (L0-L2) has
   the real, structural blind spots the
   [detectability matrix](evidence-and-detectability.md#5-what-abi-tools-cannot-prove)
   this same page opens by citing lists — macro values, inline/template
   bodies, uninstantiated templates — which is exactly what build/source
   evidence (L3/L4) exists to close, per the row above; incomplete debug
   info or headers narrows even the L0-L2 picture further still. The other
   methods are necessarily *sampling* on top of whichever tier you ran —
   a golden test proves the scenarios it encodes, an ASan run proves the
   lifecycle it exercises. Losing the systematic layer to "we have
   behavioral tests" reopens exactly the blind spot
   [Detecting Breaks §3](abi-series/08-detection.md#3-why-an-abidiff-or-abicc-class-checker-is-not-sufficient)
   describes for single-method static checkers, just moved to a different
   axis: sampled coverage instead of missing evidence tiers.

## One check per row, as a shell line

This page names no scanner command, by design: the table is about what the
*other* methods prove. Each row is nonetheless a concrete job, and stated
as one it is harder to skip:

```bash
# consumer rebuild: the consumer's own build against the new headers
cmake --build consumer-build --target all && ctest --test-dir consumer-build
# binary swap: the already-built consumer against the new library
LD_LIBRARY_PATH=new/lib ./consumer-prebuilt --self-test
# reverse swap: the new consumer against the old library
LD_LIBRARY_PATH=old/lib ./consumer-new --self-test
# host x plugin matrix: every supported pair
for h in host-1.4 host-1.5; do for p in plugin-2.0 plugin-2.1; do ./$h --load ./$p.so --smoke; done; done
# oldest-supported-OS load test
docker run --rm -v "$PWD/new/lib:/lib/foo" rockylinux:8 ldd /lib/foo/libfoo.so.1
# golden outputs, written by the old version, replayed against the new one
./tool-new --replay golden/ | diff - golden/expected.txt
# old-reader/new-writer and the reverse
./tool-new --write out.bin && ./tool-old --read out.bin && ./tool-old --write out2.bin && ./tool-new --read out2.bin
# ASan-instrumented lifecycle test
ASAN_OPTIONS=detect_leaks=1 ./consumer-asan --lifecycle-test
# TSan under a contending workload
TSAN_OPTIONS=halt_on_error=1 ./consumer-tsan --threads 16 --stress
```

## Where to go next

- [Detecting Breaks](abi-series/08-detection.md) — the static-checking side
  of this same question: which evidence tier catches which break family, and
  why no single static tool is enough on its own.
- [Limitations](limitations.md) and
  [Evidence & Detectability §5](evidence-and-detectability.md#5-what-abi-tools-cannot-prove)
  — the exact, itemized boundary of what *artifact-only* (binary/header, L0-L2)
  comparison cannot see. Several of those rows (macro-only changes, an inline
  body, an uninstantiated template) are exactly what L3/L4 source-replay
  evidence closes — see the row above — so the boundary that motivates *this*
  page's table is the one no static tool can cross: pure behavioral/semantic
  and ownership/lifetime/thread-safety changes, which stay outside static
  checking at any evidence tier.
- [CI Gating Pipeline](../use/ci-gating.md) — wiring abicheck's own static
  check into a release pipeline; the assurance methods above are the
  complementary jobs that sit alongside it, not inside it.

---

**Ladder:** ← [Detecting Breaks](abi-series/08-detection.md) · Step 6 · Detect Breaks · [Where in the Pipeline](where-in-the-pipeline.md) →
