# Case 105: Concept Tightening (C++20)

**Category:** Subtle source break / regression suite | **Verdict:** 🟠 API_BREAK

## Verdict and consumer impact

A C++20 `concept` (`Addable`) gains an additional requirement
(default-constructibility). The mangled name of the already-shipped
instantiation (`sum<int>`) is unchanged, so previously-compiled binaries
keep linking against v2's `.so` with no observable effect. The break is at
the *consumer* call site: any consumer instantiating `sum<T>` against a
type that fails the new requirement no longer compiles against v2's
headers. Object/DWARF/header (L0–L2) comparison sees nothing — this is the
catalog's canonical example of a change that needs the opt-in L4 source-ABI
replay layer to detect at all.

## Old/new diff

| v1.h | v2.h |
|------|------|
| `concept Addable = requires(T a, T b) { a + b; };` | adds `T();` to the requirement set |
| `sum<wrapped>` compiles (`wrapped` has `operator+`) | `sum<wrapped>` fails (`wrapped` has no default ctor) |

## abicheck command

```bash
g++ -std=c++20 -shared -fPIC -g v1.cpp -o libtpl_v1.so
g++ -std=c++20 -shared -fPIC -g v2.cpp -o libtpl_v2.so

# Default (object/header) comparison — documented gap, castxml isn't even
# available to try here, and clang's JSON AST dump doesn't model C++20
# concepts as named declarations either:
abicheck compare libtpl_v1.so libtpl_v2.so --header old=v1.h --header new=v2.h
# → Error: castxml not found in PATH (the documented default backend for
#   header/AST evidence; this case's own gap analysis shows even a
#   clang-frontend header AST would report NO_CHANGE, since neither AST
#   backend's *header-level* pass models concept declarations by name)

# L4 source-ABI replay actually catches it. Needs L3 build-context evidence
# (a compile_commands.json) explicitly paired with a public-header root, so
# abicheck knows the template is public and reachable, plus
# --no-scope-public-headers on compare (the default L2 header-AST public-
# surface scoping can't recognize `sum`/`Addable` as public because it
# doesn't model concepts either):
cat > v1.compile_commands.json <<EOF
[{"directory": "$PWD", "command": "c++ -std=c++20 -c v1.cpp -o v1.o", "file": "$PWD/v1.cpp"}]
EOF
cat > v2.compile_commands.json <<EOF
[{"directory": "$PWD", "command": "c++ -std=c++20 -c v2.cpp -o v2.o", "file": "$PWD/v2.cpp"}]
EOF
python3 <<'PYEOF'
import dataclasses
from pathlib import Path
from abicheck.buildsource import pack_io
from abicheck.buildsource.inline import collect_inline_pack

for v in ("v1", "v2"):
    pack = collect_inline_pack(
        sources=Path("."),
        build_info=Path(f"{v}.compile_commands.json"),
        public_header_roots=(f"{v}.h",),
        extractor="clang",
    )
    pack_io.write(dataclasses.replace(pack, root=Path(f"{v}.evidence")))
PYEOF

abicheck dump libtpl_v1.so -H v1.h -p v1.compile_commands.json --build-info v1.evidence --ast-frontend clang -o v1.abi.json
abicheck dump libtpl_v2.so -H v2.h -p v2.compile_commands.json --build-info v2.evidence --ast-frontend clang -o v2.abi.json

abicheck compare v1.abi.json v2.abi.json --no-scope-public-headers
```

## Expected abicheck finding

```text
Default (object/header) comparison: Error — castxml not installed; and per
this case's own gap analysis, even a working header-AST pass (castxml or
clang) reports NO_CHANGE, since neither backend's header-level AST models
C++20 concepts as named declarations abicheck can diff.

L4 source-ABI replay: Verdict: API_BREAK (exit 2)

- concept_tightened: Concept constraint tightened: mylib::Addable
  (sha256:a681164840a7... -> sha256:2a6ad2ca2d41...)
  > A public C++20 concept became more constrained; consumer templates or
    calls that satisfied the old constraint may no longer compile against
    the new headers.
```

## Minimum evidence

`min_evidence: L4` — L0–L2 artifact/header scans cannot observe the
constraint tightening: the binary is unaffected (same exported symbol,
same instantiation), and the default header-AST backend (castxml is the
documented reference; this sandbox has no castxml installed, but the
gap is backend-independent — see below) emits C++20 `concept` declarations
as an unnamed, bodyless node with no link to the templates that use it, so
there's nothing to diff at the header level either. Only the L4 source-ABI
replay layer (`--sources`/`--build-info`, a clang-AST-based extractor that
walks the real `ConceptDecl` node and hashes its constraint expression)
carries the fact abicheck needs. This is verified above against this
case's real `v1.h`/`v2.h` — the L4 run reports `concept_tightened` with
verdict `API_BREAK`, matching `ground_truth.json` exactly.

## Why abicheck catches it

`abicheck/buildsource/source_extractors/clang.py`'s concept emitter walks
the same clang AST used by the L4 replay path and records each concept's
name plus a hash of its `requires`-expression body.
`abicheck/buildsource/source_diff.py`'s concept diff compares that hash
across versions and reports `ChangeKind.CONCEPT_TIGHTENED` (API_BREAK) when
it changes — a source-level fact no artifact/header comparison can reach,
because concepts are pure compile-time constraints with no representation
in the compiled binary or (for either backend tested here) the header-level
AST.

## Runtime failure demonstration

**Severity: source break only — no binary impact**

**Scenario:** `app.cpp` instantiates `sum<wrapped>`, where `wrapped` has no
default constructor.

```bash
# v1: wrapped satisfies Addable (has operator+). Compiles and links.
g++ -std=c++20 app.cpp -L. -ltpl_v1 -Wl,-rpath,. -o app_v1
./app_v1
# → sum<int>(2, 3) = 5 (expect 5)

# Runtime substitution is unaffected: sum<int>'s mangled name is unchanged.
cp libtpl_v1.so libtpl.so
g++ -std=c++20 app.cpp -L. -ltpl -Wl,-rpath,. -o app_swap
./app_swap        # → sum<int>(2, 3) = 5
cp libtpl_v2.so libtpl.so
./app_swap        # → sum<int>(2, 3) = 5   (no recompile, no change)

# Source rebuild against the v2 header is what actually breaks:
sed 's/"v1.h"/"v2.h"/' app.cpp > app_v2.cpp
g++ -std=c++20 -c app_v2.cpp -o app_v2.o
# → error: static assertion failed
# → note: the required expression 'T()' is invalid
```

**Why source-only:** the tightened constraint is checked entirely at
template-instantiation time in the consumer's own translation unit; the
library's shipped `sum<int>` instantiation and its mangled symbol never
change, so an already-built binary keeps working forever — but nobody can
recompile `app.cpp` against v2's headers with a non-default-constructible
`T`.

## Safe redesign

- Stage the tightening across a deprecation window: ship the looser concept
  alongside a deprecated alias that warns, then remove.
- Provide a SFINAE-friendly migration: expose a second template that
  preserves the old contract, marked `[[deprecated]]`.
- For internal-only concepts, prefix with `detail::` and document them as
  not-API.

**Real-world example:** concept tightening is the C++20 evolution of the
older SFINAE-narrowing pattern (`std::enable_if<...>`); algorithm-heavy
libraries (oneTBB, the standard library) narrow the set of types a template
accepts this way, sometimes to fix a latent bug, sometimes to nudge users
toward "better" types — and every such tightening is a silent source-break
for whoever was relying on the relaxed contract.

## References

- [P0892R2 `concept` definition syntax](https://wg21.link/P0892R2)
- [cppreference: `requires`-expression](https://en.cppreference.com/w/cpp/language/requires)
- castxml limitation: concepts emitted as `<Unimplemented kind="Concept"/>`
  (no name, no body) — see [castxml issue tracker](https://github.com/CastXML/CastXML/issues?q=concept).
