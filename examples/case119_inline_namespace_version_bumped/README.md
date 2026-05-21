# case112 — inline namespace version bumped (BREAKING)

## What this case demonstrates

A library uses a versioned inline namespace (`inline namespace _V1`) to
manage ABI evolution. Between v1 and v2 the version segment is bumped
to `_V2`. Source still compiles, but every freshly built TU emits a
different mangled symbol — old and new TUs of the same program ODR-
violate when linked together.

| v1 declares | v2 declares |
|---|---|
| `lib::_V1::sort`, `lib::_V1::unique` (inline) | `lib::_V2::sort`, `lib::_V2::unique` (inline) |

## Why a dedicated detector

The existing `INLINE_NAMESPACE_MOVED` detector is symbol-table driven
and requires ≥2 mangled-symbol moves. It works for built shared
libraries but misses:

- header-only / template libraries that ship no `.so`
- single-symbol bumps
- pure declaration-level snapshots (castxml header dumps)

`INLINE_NAMESPACE_VERSION_BUMPED` fires from declared qualified names
alone, so it catches the bump even on header-only inputs and on a
single declaration.

## Expected verdict

`BREAKING` — mangled names change, so any program that mixes old and
new TUs at link or load time is broken.
