<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **Directory and package `compare` no longer refuses three of four members on a multi-member release** — the per-member header narrowing added in `882482c47` (#1554) gave each member only the headers its exported names appeared in, decided per side. On a real four-member release this left one member with a header subset that did not parse and refused two members as `NOT_COMPARABLE` (`scope_fingerprint` mismatch), moving the exit code from `4` (binary break) to `16`. Every member again parses the release's full `-H` set on both sides. The verdict-scoping of types a member's own exports prove unreachable is kept. The fan-out notice now announces a memory clamp only when memory is the limit that binds; an interpreter-bound pool is silent, as before.
