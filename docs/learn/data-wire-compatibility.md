---
doc_type: explanation
audience:
  - library-maintainer
level: advanced
canonical_for:
  - data-wire-compatibility
depends_on:
  - abicheck/diff_types.py
lifecycle: active
generated: false
---

# Data, Wire & Storage Compatibility

A static comparison cannot decide this dimension;
[§5 of Evidence & Detectability](evidence-and-detectability.md#5-what-abi-tools-cannot-prove)
says why. [Compatibility as a Product Contract §2](abi-series/00-product-contract.md#2-compatibility-is-not-one-question-name-which-kind-you-mean)
lists this as its own dimension because it is easy to conflate with binary
ABI compatibility — both are about layout and values — while answering a
different question for a different, often much longer-lived, audience.


## The question this dimension answers

Binary compatibility asks whether an *already-compiled consumer* keeps
working. Data compatibility asks whether **values that cross a boundary
outside the process** — a serialized file, a network message, a shared-memory
segment, an on-disk database record, a config file — are still interpreted
correctly, by *any* reader, including ones compiled long before or after the
writer, on a different machine, possibly written in a different language
entirely.

That last clause is the important difference: an ABI consumer is bound to
one specific build of your library, linked in the same process. A data
consumer can be a file written last year, read by next year's version of
your tool, or a message produced by a C++ service and consumed by a Python
one that only knows the wire format, never your headers.

## Where this dimension hides inside an ordinary ABI change

The same C/C++ construct commonly serves two purposes at once — as an
in-memory ABI type *and* as a wire/storage format — and a change that's
perfectly fine for the first purpose can be a real break for the second:

- **Enum values.** [Part 3 — Type Layout](abi-series/03-type-layout.md)
  covers `enum` as an ABI/layout concern (underlying type size, whether a
  value fits) — reassigning which name maps to which number is itself an
  ABI-relevant change abicheck detects as `enum_member_value_changed`
  (`BREAKING`: a compiled consumer embeds the numeric constant directly, so
  an old binary keeps calling the old number by the new name's meaning). If
  an enum's numeric values are *also* persisted — written to a file, sent
  over a socket, stored in a database column — the same reassignment is a
  data-compatibility break too, for a reader that was never recompiled at
  all: a file or message written under the old mapping is misread by any
  consumer, old binary or freshly-rebuilt source, that only knows the new
  one.
- **Struct layout used as a wire format.** A struct passed by value across
  an API boundary is an ABI concern; the *same struct* memcpy'd into a file
  or a network buffer is now also a wire-format concern, and every ABI
  layout change (`type_size_changed`, `type_field_offset_changed`,
  `struct_packing_changed`, endianness assumptions baked into raw byte
  layout) is also potentially a data-format break for every file or message
  already written in the old layout.
- **Bit-field and flag layout.** Persisted bitmask values assume specific
  bit positions. With layout evidence available (debug info, or header AST
  from a backend that computes bit offsets), abicheck does detect a moved
  bit-field as an ordinary `type_field_offset_changed` ABI finding — the
  data-compatibility risk here is the same "you get the signal, not the
  framing" case as the struct-layout row above, not an invisible one; it
  only becomes genuinely invisible on an evidence-poor input (a stripped
  binary with no headers, or a header backend that can't compute bit
  offsets) where the ABI signal itself is unavailable.
- **Version tags and magic numbers.** Formats often self-describe via a
  leading version field — changing what a version number *means*, or
  reusing one, is a data-compatibility break with no ABI signature at all.

## Why abicheck's model doesn't cover this directly

abicheck's evidence model (see
[Evidence & Detectability](evidence-and-detectability.md)) is built around
the compile/link/load boundary — what a *compiled consumer* observes about a
*library's* declarations and exports. It has no concept of "this struct is
also a file format" or "this enum's numeric value is persisted" — that
relationship exists only in your application's design, not in anything a
snapshot of symbols, types, or headers records. A struct layout change is
reported the same way (as a layout finding) whether the struct is a pure
in-memory ABI type or also a wire format; abicheck cannot tell you which
consequence applies, because it doesn't know the struct is used as a wire
format at all.

This means: **every data/wire-compatibility break that happens to also be an
ABI-relevant change (layout, enum values) surfaces as an ordinary ABI
finding when the evidence for it was actually collected** — you get the
signal, just not the framing, and only at the evidence depth that finding
needs (see [Evidence & Detectability](evidence-and-detectability.md) for
which `--depth`/evidence layer each kind requires; a stripped, headerless
L0-only comparison sees neither layout nor enum-value facts at all, so a
clean L0 result is not a wire-safety signal for either). What abicheck
cannot see, at any evidence depth, is a data-compatibility break with *no*
ABI signature at all —
e.g. an application-level serialization routine that changes wire format
independent of any C/C++ type it touches (a custom binary writer choosing a
different byte order or field order, a protocol buffer schema evolved
outside the C++ type that carries it, a JSON shape change), where nothing
about the C/C++ declarations themselves moved.

## Designing for it

- **Never persist a raw enum's underlying integer as the format** without an
  explicit, documented, append-only mapping — treat the mapping itself as
  the durable contract, not the enum declaration.
- **Don't treat a raw, memcpy'd struct as a portable wire format at all** —
  `#pragma pack` is compiler-specific, not a wire-format standard, and even
  with it pinned, byte order and object representation still aren't
  guaranteed to match across platforms, architectures, or languages. If a
  raw-layout format is genuinely unavoidable (a tightly-controlled,
  single-platform IPC channel, say), define it field-by-field with explicit
  offsets, explicit endianness, and validation on read — never "whatever the
  compiler currently does with this struct." Otherwise, prefer explicit
  field-wise (de)serialization or a schema-driven format, below, over any
  form of raw struct layout.
- **Version the format explicitly**, and never reuse a version number for an
  incompatible layout.
- **Use a schema-driven serialization format** (protocol buffers, FlatBuffers,
  Cap'n Proto, a hand-rolled TLV format) for anything crossing a
  process/language/version boundary, rather than raw struct memcpy, exactly
  because these formats separate the wire contract from the in-memory ABI
  and make its evolution rules explicit and tool-checked.
- **Test round-trips across versions** — write with the old version, read
  with the new one, and the reverse — as part of the release process; this
  is the data-compatibility analog of the regression-test guidance in
  [Behavioral & Semantic Compatibility](behavioral-compatibility.md), for
  the same underlying reason: no static analyzer proves this, execution
  evidence does.

As one shell line, the check is a file written by the old version and read
back by the new one, then the reverse:

```bash
./tool-old --write sample.dat && ./tool-new --read sample.dat && ./tool-new --write sample2.dat && ./tool-old --read sample2.dat
```

See also: [Type Layout](abi-series/03-type-layout.md) for the ABI-layout
mechanics this dimension frequently piggybacks on, and
[Behavioral & Semantic Compatibility](behavioral-compatibility.md) for the
adjacent question of whether an *operation's* meaning, not a *value's*
meaning, stayed the same.

---

**Ladder:** ← [Behavioral & Semantic Compatibility](behavioral-compatibility.md) · Tier 8 · Beyond static ABI · [Ownership & Lifetime Contracts](ownership-and-lifetime.md) →
