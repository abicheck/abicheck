# Case 89: Inline Accessor References Renamed Pimpl Member

**Category:** Pimpl ABI | **Verdict:** 🔴 BREAKING

## Verdict and consumer impact

```cpp
class descriptor {
public:
    inline int get_class_count() const { return impl_->class_count_; }
private:
    detail::pimpl<detail::descriptor_impl> impl_;
};
```

v2 renames `detail::descriptor_impl::class_count_` to `n_classes_` (and
reorders the fields) as part of a "modernize naming" cleanup, updating the
inline accessor body in lockstep. Rebuilding the library succeeds.
Rebuilding a *new* consumer succeeds. But an **existing consumer binary**
compiled against v1.h has the old inline body — `return
impl_->class_count_` at its v1 byte offset — baked directly into its own
code. Run against v2's reordered `descriptor_impl`, that inline read lands
on a different field entirely. There is no symbol-level evidence
(`get_class_count` is inline, no exported symbol) and no public-type layout
change (`descriptor` still holds one pimpl pointer) — the break lives
entirely in the gap between what the consumer's inline body assumes and
what the new detail layout actually is.

## Old/new diff

| v1.h (`detail::descriptor_impl`) | v2.h (`detail::descriptor_impl`) |
|------|------|
| `int class_count_ = 2;` | `int n_classes_ = 2;` *(offset moved)* |
| `int max_iter_ = 100;` | `int iteration_cap_ = 100;` *(offset moved)* |

## abicheck command

```bash
g++ -shared -fPIC -g -std=c++17 -I. v1.cpp -o libfoo_v1.so
g++ -shared -fPIC -g -std=c++17 -I. v2.cpp -o libfoo_v2.so
abicheck compare libfoo_v1.so libfoo_v2.so \
  --ast-frontend clang -H old=v1.h -H new=v2.h --lang c++
```

## Expected abicheck finding

```text
Verdict: BREAKING (exit 4)

- inline_body_references_renamed_member: Public class 'descriptor' has
  inline accessors (2 found) reaching into 'mylib::detail::descriptor_impl'
  by name. Field 'class_count_' was renamed to 'iteration_cap_' in the new
  internal layout. Consumers compiled against the old header have the old
  member name baked into their inline accessor bodies; running against the
  new library reads the wrong offset or fails to resolve the member.
  (class_count_ -> iteration_cap_)

Source-Level Breaks:
- field_renamed: descriptor_impl::class_count_ -> iteration_cap_
- field_renamed: descriptor_impl::max_iter_ -> n_classes_
```

## Minimum evidence

`min_evidence: L2` — the field rename alone (visible at L1, from DWARF
layout) doesn't distinguish "internal detail renamed, harmless" from
"internal detail renamed, and a public inline accessor's header-emitted
body reaches into it by name". The public header AST is what confirms
`descriptor::get_class_count()` is inline (its body ships into every
consumer) and that its body's member-access expression names the exact
field that got renamed.

## Why abicheck catches it

`detect_inline_body_references_renamed_member`
(`abicheck/diff_cpp_patterns.py`) correlates two independently-unremarkable
facts: (1) a field rename on a record type in an internal namespace
(`detail::`, via the same `is_internal_type` reachability logic as the
internal-leak detectors), and (2) a public class's inline method — no
exported symbol, body only visible via DWARF/header — whose member-access
expression names the old field. When both match, it emits one
`inline_body_references_renamed_member` finding describing the full chain
instead of leaving the field rename looking like harmless internal churn.

## Runtime failure demonstration

**Severity: CRITICAL**

**Scenario:** compile app against v1.h (baking `impl_->class_count_` at its
v1 offset into the app's own inline code), then swap in v2's library
without recompiling.

```bash
# Build old library + app
g++ -shared -fPIC -g -std=c++17 -I. v1.cpp -o libfoo.so
g++ -g -std=c++17 -I. app.cpp -L. -lfoo -Wl,-rpath,. -o app
./app
# → class_count = 2   (exit 0)

# Swap in new library (no recompile)
g++ -shared -fPIC -g -std=c++17 -I. v2.cpp -o libfoo.so
./app
# → class_count = 100   (exit 1 — silently wrong, not a crash)
```

**Why CRITICAL:** the v2 fields were also reordered
(`iteration_cap_`/`n_classes_` swap `max_iter_`/`class_count_`'s slots), so
the app's v1-compiled inline read at offset 0 now lands on `iteration_cap_`
(value `100`) instead of `class_count_` (value `2`) — silent wrong data
with no crash, no linker error, and no source-level signal at the call
site.

## Safe redesign

Never let a public inline accessor reach into a `detail::`/pimpl member by
name — the accessor's body ships into every consumer binary and bakes in
whatever offset the field had at compile time. Move accessors like
`get_class_count()` out-of-line into the library (a real exported symbol
that can be recompiled independently), or keep the pimpl type's field names
and order frozen for the life of the ABI even during "harmless" internal
renames.

**Real-world example:** oneDAL's pimpl idiom plus inline header accessors
creates exactly this risk surface for every `detail::*_impl` field — the
class-member rename is invisible to users at source level but propagates
into every consumer binary already compiled against the previous header.

## Cross-tool comparison

```bash
abidw --out-file v1.xml libfoo_v1.so
abidw --out-file v2.xml libfoo_v2.so
abidiff v1.xml v2.xml
```

Not independently re-verified in this environment (`abidiff` unavailable
here) — see case07's struct-layout case for a documented `abidiff`
exit-code comparison on a related layout-change finding.
