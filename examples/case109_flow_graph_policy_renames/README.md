# Case 109: flow::graph Policy Tag Renames

**Category:** Source API / oneTBB regression suite | **Verdict:** 🔴 BREAKING

## What breaks

A header-only set of policy-tag types (`queueing`, `rejecting`) used as
template parameters for `function_node` is renamed wholesale
(`buffering_policy`, `backpressure_policy`). The instantiation anchor
typedef (`queue_node`) is also dropped and replaced. Consumer source
that wrote any of the v1 names fails to compile against v2 headers.

Because the offending types are header-only template parameter tags,
**no exported library symbol changes**: the `mylib_flow_run` ABI is
identical between v1 and v2. The .so will continue to link with old
consumers, but any rebuild against the new headers breaks.

## Why this is in the oneTBB regression suite

Mirrors oneTBB 2021's reshuffle of `tbb::flow::*` policy types — a
documented historical break from the upstream maintainers.

## How abicheck catches it

The diff exposes:

- `TYPEDEF_REMOVED`: `queue_node`
- `TYPE_REMOVED`: `queueing`, `rejecting`
- `TYPE_ADDED`: `buffering_policy`, `backpressure_policy`
- `TYPEDEF_ADDED` (informational): `buffer_node`

That set is enough to classify the diff as API_BREAK without any new
detector. Use `member_name` / `type_pattern` suppressions if a downstream
consumer wants to silence informational additions.

## Code diff

| v1 | v2 |
|----|------|
| `struct queueing {};` | `struct buffering_policy {};` |
| `struct rejecting {};` | `struct backpressure_policy {};` |
| `typedef function_node<queueing> queue_node;` | `typedef function_node<buffering_policy> buffer_node;` |

## How to fix (as a library maintainer)

- Stage renames across a deprecation cycle: in release N–1, declare the
  new names and add `using queueing = buffering_policy;` aliases marked
  `[[deprecated]]`. In release N, remove the old tags.
- Audit downstream consumers (build farms, sample repos) before
  removing legacy aliases — header-only renames look invisible to
  binary-only ABI checkers, so a "compiles for me" test sweep on
  representative consumers is the only reliable signal.

## References

- oneTBB 2021 migration guide: `flow::graph` node policy updates.
