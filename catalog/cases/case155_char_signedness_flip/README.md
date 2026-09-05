# case155_char_signedness_flip — Plain-`char` signedness flip (`-fsigned-char` ↔ `-funsigned-char`)

**Category:** Risk | **Verdict:** 🟡 COMPATIBLE_WITH_RISK

## Verdict and consumer impact

v1 and v2 share identical source and identical exported symbols — v1 was
built with `-fsigned-char`, v2 with `-funsigned-char`. `char`, `signed
char`, and `unsigned char` are three distinct types in C/C++; a plain-`char`
parameter or struct member reinterprets the same bytes with the opposite
sign once a consumer is recompiled against the other setting. Comparisons
(`if (c < 0)`), sign-extension on assignment to a wider integer, and
`printf`-style range checks all silently change behavior with no compiler
error and no link error.

## Old/new diff

| Build flags | v1 | v2 |
|---|---|---|
| plain-`char` signedness | `-fsigned-char` | `-funsigned-char` |

Source and exported symbols are byte-for-byte identical between v1 and v2;
only this compile option differs.

## abicheck command

The case ships `old.json`/`new.json` as hand-built `BuildEvidence` fixtures
(the normalized L3 model `dump --build-info`/`--sources` would produce from a
real build) rather than compiled binaries, so reproducing the finding means
embedding each side's fixture into a snapshot's `build_source` field —
exactly what `dump --build-info` does internally — and comparing the two:

```bash
python3 - <<'PY'
import json
from pathlib import Path
from abicheck.model import AbiSnapshot
from abicheck.buildsource.pack import BuildSourcePack
from abicheck.buildsource.build_evidence import BuildEvidence
from abicheck.serialization import save_snapshot

for side in ("old", "new"):
    d = json.loads(Path(f"{side}.json").read_text())
    snap = AbiSnapshot(library="libdemo.so", version="1")
    snap.build_source = BuildSourcePack(root=Path(""), build_evidence=BuildEvidence.from_dict(d))
    save_snapshot(snap, f"{side}.abi.json")
PY

abicheck compare old.abi.json new.abi.json
```

## Expected abicheck finding

```text
Verdict: COMPATIBLE_WITH_RISK (exit 0)

Deployment Risk Changes:
- char_signedness_changed: Runtime-model option 'char_signedness' changed: 'signed' -> 'unsigned'.
  > May not be link- or runtime-compatible across consumers; the artifact
    diff confirms any concrete break.
```

## Minimum evidence

`min_evidence: L3` — the compile option itself carries the fact. Plain-`char`
signedness never appears in the exported symbol table and is not
distinguished in most layout dumps, so only the captured build flag exposes
it.

## Why abicheck catches it

`abicheck compare` reads each side's normalized `BuildEvidence.build_options`
(as embedded by `dump --build-info`/`--sources`, or supplied out-of-band via
`--old/new-build-info`) and diffs the `char_signedness` option directly —
the same `diff_build_evidence()` routine `tests/test_l3l4l5_examples.py`
exercises against the committed fixtures. Because the platform default for
plain-`char` signedness is target-dependent (signed on x86, unsigned on
most ARM targets), abicheck requires **both** sides to state the flag
explicitly before reporting a flip, avoiding a false finding on a project
that merely records its platform default. Per ADR-028 D3 this build-evidence
finding never decides a shipped-ABI break on its own — it flags the
elevated risk and localizes the cause; an artifact diff of actual observed
values is what would confirm a concrete break.

## Runtime failure demonstration

There's no compiled `app.c` consumer for this case — the failure mode is a
cross-toolchain one, not a single process crash. Picture a library built
for x86_64 (`-fsigned-char` is GCC's default there) and cross-compiled for
an ARM target where `-funsigned-char` is the platform default, both from
identical source and shipping identical symbols: a consumer that does
`if (c < 0)` on a plain-`char` field behaves correctly on one platform and
never on the other, with no build failure anywhere. A CI job that diffs
captured build options across platform-specific build configurations — not
just across releases — is exactly what would catch this.

## Safe redesign

Pin one char signedness across the library and its consumers (`-fsigned-char`
or `-funsigned-char` explicitly, rather than relying on the platform
default), or avoid plain `char` in public interfaces entirely — use
`signed char`/`unsigned char` (or `int8_t`/`uint8_t`) where the sign matters.

## Cross-tool comparison

`abidiff`/`abi-compliance-checker` compare built binaries (symbols + DWARF);
neither reads compile options, and plain-`char` signedness is not
distinguished as a separate type in DWARF, so two builds from identical
source under different `-f{signed,unsigned}-char` settings produce no diff
for either tool. Only the L3 build-evidence layer that abicheck reads
directly localizes the cause to the signedness flag flip.
