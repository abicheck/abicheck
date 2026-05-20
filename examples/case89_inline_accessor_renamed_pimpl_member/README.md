# Case 89: Inline accessor references renamed pimpl member

**Category:** Pimpl ABI | **Verdict:** BREAKING

## What breaks

The public class exposes inline getters that reach into the pimpl
implementation by member name:

```cpp
class descriptor {
public:
    inline int get_class_count() const { return impl_->class_count_; }
private:
    detail::pimpl<detail::descriptor_impl> impl_;
};
```

In v2, `detail::descriptor_impl::class_count_` is renamed to `n_classes_`
during a "modernize naming" cleanup. The maintainer updates the inline
accessor body in the header in lockstep, so:

- Rebuilding the **library** succeeds.
- Rebuilding **new consumers** against v2 headers succeeds.
- **Existing consumer binaries** — compiled against v1.h with the old
  inline body baked in — continue to reference `class_count_` at the
  old offset. Linked against v2's `descriptor_impl` layout, the inline
  access reads the wrong field (or garbage if the layout shifted).

There is no symbol-level evidence of the break: `descriptor::get_class_count`
is inline and has no exported symbol. There is no public-type layout change:
`descriptor` still holds one pimpl pointer. The break lives entirely in the
gap between *what the consumer's inline body assumes* and *what the new
detail layout actually is*.

## Why distinct from case35 + case47

- case35 (`field_rename`) covers field renames in isolation. It does not
  exercise the inline-consumer-baked-in-body trap.
- case47 (`inline_to_outlined`) is the COMPATIBLE counter-case (moving an
  inline to outlined is safe).
- case89 is the *interaction*: field rename in a detail:: type, accessed
  by an inline public method whose body is shipped into consumers.

## How abicheck catches it

The new `inline_body_references_renamed_member` detector correlates:

1. A `field_renamed` (or `type_field_removed` + `type_field_added`) on a
   record type that lives in an internal namespace (reuses
   `internal_leak.py::is_internal_type`).
2. An inline public method (presence of body in DWARF, no exported
   symbol) on a *non-internal* type whose member-access expression
   references the old field name.

When both halves match, emit a single
`INLINE_BODY_REFERENCES_RENAMED_MEMBER` finding describing the chain
`descriptor::get_class_count() (inline) → impl_->class_count_ (renamed)`.

## Real-world reference

oneDAL's pimpl idiom plus inline header accessors creates this exact
risk surface for every `detail::*_impl` field. The class member-rename
is invisible to users at source level but propagates into every
consumer binary already compiled against the previous header.
