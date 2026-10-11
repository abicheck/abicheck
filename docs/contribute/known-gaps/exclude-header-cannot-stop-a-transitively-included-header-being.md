---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# `--exclude-header` cannot stop a transitively-included header being analyzed

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-11 at `46c127111`): OPEN.** `extract/header_exclusions.py::apply_header_exclusions_to_inputs` still filters only the resolved (directory-expanded) header list; nothing prunes a header reached through another header's `#include`. Unmatched patterns now warn (`unmatched_exclusion_patterns`), which does not address the transitive case.


**Update (2026-10): the descriptor half is moot.** The ABICC `compat` front
end, its XML descriptors and `model/header_skip_rules.py` were removed, so no
run reads `<skip_headers>`/`<skip_including>` any more. The limitation below
still holds for native `--exclude-header`, which shares it exactly.

**The glob question this entry used to record is closed.** It asked whether
real ABICC globs in `<skip_headers>`, and left descriptor skips matched as
exact basenames or paths in the meantime -- under which every tree-relative
rule (Intel MKL's own `fftw/fftw.h`, `fftw/offload/`) matched nothing at all
while the run still produced a confident verdict. The evidence the entry
asked for is in ABICC's own source: `Internals/Path.pm`'s `classifyPath` and
`Internals/Filter.pm`'s `skipHeader_I` classify each rule into one of three
classes -- a bare name matched against the basename, a value containing a
separator matched against the path at component boundaries (including a
directory's descendants), and a metacharacter-bearing value compiled as a
regex-like pattern. `abicheck/model/header_skip_rules.py` is that
classification, and the descriptor's snapshot now records
`excluded_header_matching = "abicc"` rather than `"exact"`, since the same
text genuinely names a different set of headers under the two rules.

Rewriting every rule to a bare basename was considered and rejected: it fixes
MKL and over-excludes an unrelated `version.h` under a different subtree, and
no example test drawn from the reported case can tell the two apart.

**What is still open** is the other half of `<skip_headers>`'s meaning. ABICC
distinguishes two elements -- `<skip_headers>` is "do not include *and do not
analyze*", `<skip_including>` is "do not include directly, but still analyze
when reached" -- and the two are now modelled separately, with only
`<skip_headers>` recorded as an achieved narrowing of the surface. Both
correctly drop a header from the *direct* `-H` operand list. Neither can stop
a header being parsed when another header reaches it through its own
`#include`, because filtering happens on the resolved header list after the
directory walk and before any parse. The native `--exclude-header` path
shares that limitation exactly (`extract/header_exclusions.py`), which is why
closing it is a post-parse filter by defining header rather than a change to
either rule language -- and why it is recorded here rather than patched on
the descriptor side alone.

Until then, `docs/reference/abicc-format-compliance.md` states partial
support for `<skip_headers>`, and the two elements' remaining difference is
in what a run *records*, not in what it parses.
