# Case 96: Hidden Friend Operator Removed

**Category:** Source API contract | **Verdict:** 🟠 API_BREAK

## What breaks

A non-member operator (`operator==`) was declared as a **hidden friend**
inside the class body — i.e. as an in-class `friend` declaration with an
inline definition. Hidden friends are findable only via [argument-dependent
lookup (ADL)](https://en.cppreference.com/w/cpp/language/adl); they have
no namespace-scope declaration that consumers can name. When the
declaration is removed in v2, every consumer that wrote `a == b` against
v1's header fails to compile against v2's. The library's `.so` is byte-
identical (the inline friend never had a public symbol to remove), so the
break is invisible to any binary-only diff tool.

## Why this is a breaking change

Hidden friends are the idiomatic C++17+ way to declare ADL-only
operators on a type (`operator==`, `operator<<`, `swap`, etc.) without
polluting the surrounding namespace — an idiom used widely across C++
libraries (for example oneTBB, oneDAL, Boost, and the standard library).
Removing one looks like a
"cosmetic header cleanup" that the maintainer believes is binary-safe —
which it *is*, at the link layer. It's the source-recompile that explodes.

## How abicheck catches it

New ChangeKind `HIDDEN_FRIEND_REMOVED` (API_BREAK). The dumper reads
castxml's `befriending` attribute on the `Class` / `Struct` element —
a whitespace-separated list of ids that point to the function elements
that were declared as in-class `friend`. Each such function is marked
`is_hidden_friend=True` in the snapshot. The diff scans for hidden
friends present in v1 but absent in v2 and emits HIDDEN_FRIEND_REMOVED.

The symmetric kind `HIDDEN_FRIEND_ADDED` is COMPATIBLE (pure addition;
existing code keeps compiling, new operator only participates at call
sites that trigger ADL).

### Complementary findings on out-of-line friends

If the hidden friend was *also* defined out-of-line (so it has a real
exported symbol), removal additionally fires `FUNC_REMOVED` at the
binary layer. Both findings are emitted — the API_BREAK reflects the
source-level ADL break and the BREAKING reflects the link-level break.

### Tri-state on `is_hidden_friend`

DWARF-only snapshots and any snapshot produced by a dumper that predates
this field set `is_hidden_friend=None`. The transition detector (in
`_check_function_signature`) skips when either side is None, mirroring
the explicit-ctor detector's handling of schema evolution.

## Code diff

| v1 | v2 |
|----|------|
| `friend bool operator==(...) { ... }` inside `point` | declaration removed |
| `Class befriending="_34"` in castxml output | `befriending` attribute absent |
| `a == b` compiles for any consumer | `a == b` fails to compile |

## Real Failure Demo

**Severity: API BREAK** (source-only — binaries keep linking)

```bash
# v1 header, v1 .so: compiles and runs.
g++ -std=c++17 -I. app.cpp -L. -lmylib -o app
./app   # → a == b → true (expect true)

# v2 header, v2 .so: app.cpp does `bool eq = (a == b);`, which
# resolves via ADL to the hidden friend `operator==` of `mylib::point`.
# v2 removes the friend declaration; ADL has nothing to find, so the
# same app.cpp source line no longer compiles:
g++ -std=c++17 -I. app.cpp -L. -lmylib -o app
# → error: no match for 'operator==' (operand types are 'mylib::point'
#          and 'mylib::point')
```

## How to fix

- Keep the hidden friend (preferred) — it's an ADL contract that
  downstream code relies on.
- If you genuinely need to remove it, ship a deprecation cycle:
  inline-define it to call a new explicit comparator (`equals(a, b)`)
  for one release, then remove.
- Provide a free function at namespace scope as a migration path:
  `bool operator==(const point&, const point&);` outside the class.

## References

- [C++17 hidden friend idiom — Walter Brown, "Hidden Friends"](https://wg21.link/N4474)
- [castxml output — `befriending` attribute on Class/Struct](https://github.com/CastXML/CastXML/blob/master/doc/manual/castxml.1.rst)
- [cppreference: argument-dependent lookup](https://en.cppreference.com/w/cpp/language/adl)
