# Case 81: Serialization tag ID reassigned

**Category:** Payload ABI | **Verdict:** BREAKING

## What breaks

DAAL's classic API persists models via `daal::services::SerializationIface`,
which assigns each serializable class a `uint64` tag ID. Files written by v1
embed these IDs; on load, a registry maps `tag_id → factory`.

If a maintainer reassigns an ID — e.g. swaps `knn_model_tag` and
`linear_regression_tag` because they were "out of alphabetical order" — every
existing saved model becomes silently corrupt:

- A `.dat` file written by v1 starts with bytes encoding `knn_model_tag = 0x1002`.
- Loaded against v2, the registry looks up `0x1002` and gets back the
  `linear_regression` factory.
- Deserialization proceeds without any error (the bytes parse as a different
  but structurally similar class) and returns the wrong type.

There is **no symbol change**, **no layout change**, **no link error**, **no
load error**. Every conventional ABI checker reports COMPATIBLE.

## Why this is its own ChangeKind

This is a *payload-level* invariant — the binary contract between two
different runs of the program separated by serialization. ChangeKinds today
track types, symbols, and layouts; tag-ID reassignment leaves all three
intact. A new `SERIALIZATION_TAG_CHANGED` ChangeKind gives this break a
name and lets policy files require explicit acknowledgement of such changes.

## How abicheck detects it

The new detector inspects exported variables and constants whose name matches
a serialization-tag convention (configurable; defaults include
`*_serialization_tag`, `*_tag`, `kSerializationTag`, `SERIALIZATION_TAG`,
DAAL's `*SerializationTag` pattern). If two such constants in the same
snapshot swap values between versions, a `SERIALIZATION_TAG_CHANGED` finding
is emitted for each.

## Code diff

```cpp
// v1.h
constexpr std::uint64_t knn_model_tag         = 0x1002;
constexpr std::uint64_t linear_regression_tag = 0x1003;

// v2.h — values swapped
constexpr std::uint64_t knn_model_tag         = 0x1003;
constexpr std::uint64_t linear_regression_tag = 0x1002;
```

## Real-world reference

`daal::services::SerializationIface::getSerializationTag()` returns an `int`
that uniquely identifies the class for persistence. The implementation is
sprinkled across `cpp/daal/include/algorithms/*/_model.h` files using
`DAAL_SERIALIZATION_TAG` macros. The tag IDs are part of the persisted
file format — changing them is on par with changing a wire protocol field.
