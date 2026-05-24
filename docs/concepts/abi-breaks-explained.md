# Examples & Breakage Guide

The `examples/` directory contains real-world ABI/API break scenarios — 74
cases in total. Each case has paired `v1`/`v2` source files, a consumer
(`app.c`/`app.cpp`), a CMake build, expected verdicts, and a `README.md`
walkthrough. Run any case yourself:

```bash
cd examples/case01_symbol_removal
gcc -shared -fPIC -g v1.c -o libv1.so
gcc -shared -fPIC -g v2.c -o libv2.so
abicheck compare libv1.so libv2.so --old-header v1.h --new-header v2.h
```

Expected verdicts for all cases are in
`examples/ground_truth.json`.
For the complete list of detected change types, see the
[Change Kind Reference](../reference/change-kinds.md).

!!! tip "Looking for a specific case?"
    Every case has its own rendered page under
    [**Examples & Case Encyclopedia**](../examples/index.md), generated from the
    per-case `README.md` plus `ground_truth.json`. Browse
    [by verdict](../examples/index.md#verdict-distribution) (🔴 BREAKING,
    🟠 API_BREAK, 🟡 COMPATIBLE_WITH_RISK, 🟢 COMPATIBLE, ✅ NO_CHANGE) or
    [by category](../examples/index.md#browse-by-category).

The sections below give a *conceptual* tour of the major break families with
representative cases. For the full catalog (all 74 cases, kept in sync
automatically), use the Encyclopedia.

## Categories

| Category | Cases | Verdict |
|----------|-------|---------|
| **Symbol/function removal** | 01, 12 | BREAKING |
| **Signature changes** (params, return type) | 02, 10 | BREAKING |
| **Struct/class layout** | 07, 14, 36, 40 | BREAKING |
| **Enum changes** (values, removal) | 08, 19, 20 | BREAKING |
| **C++ vtable / virtual methods** | 09, 23, 38 | BREAKING |
| **Global variable type** | 11 | BREAKING |
| **Qualifier changes** (const, static) | 21, 22, 30, 39 | BREAKING |
| **Template / dependency** | 17, 18 | BREAKING |
| **Pointer level / typedef** | 28, 33 | BREAKING |
| **Field rename / base class** | 35, 37 | BREAKING |
| **Union field removed** | 24 | BREAKING |
| **Enum/field rename** (API-only) | 31, 34 | API_BREAK |
| **Additive changes** (new symbol, enum member) | 03, 25, 26b, 29 | COMPATIBLE |
| **Policy / metadata** (SONAME, visibility, versioning) | 05, 06, 13, 27 | COMPATIBLE |
| **Inline / noexcept** | 15, 16, 47 | COMPATIBLE |
| **No change** | 04, 32 | NO_CHANGE |

---

## Detailed case walkthroughs

Each case below includes a minimal code diff, a consumer-side example showing how downstream code is affected, and practical mitigation advice.

---

## case01_symbol_removal — exported symbol removed

```c
/* lib v1 */
int foo_init(void);

/* lib v2 */
/* int foo_init(void); removed */
```

```c
/* consumer (built against v1) */
extern int foo_init(void);
int main(void) { return foo_init(); }
```

If `foo_init` is removed from the dynamic symbol table, the consumer binary still contains an import
for that symbol and expects the loader to resolve it from the shared object. With v2 installed, symbol
resolution fails before application logic starts, typically with `undefined symbol: foo_init`. This is
a hard ABI break because the already-built consumer artifact becomes non-runnable without recompilation.

To avoid this, keep the old symbol as a compatibility wrapper (possibly deprecated) and forward it to
new implementation. Remove only in a major ABI line and bump SONAME so package managers and users can
co-install or consciously migrate.

## case02_param_type_change — parameter ABI changed

```c
/* lib v1 */
double process(int a, int b);

/* lib v2 */
double process(double a, int b);
```

```c
/* consumer (compiled against v1) */
extern double process(int, int);
double run(void) { return process(3, 4); }
```

Parameter type changes alter the call contract. On x86-64 SysV ABI, `int` is passed in an integer
register (`%edi`) while `double` is passed in an SSE register (`%xmm0`). An old caller compiled for
`int` puts the value in the wrong register class — the new callee reads uninitialized `%xmm0`,
producing garbage. The symbol name stays the same in C, but call semantics are completely different.

Safe evolution pattern: keep `process(int, int)` and introduce `process_v2(double, int)`. Route old API
to new internals where possible, but preserve old symbol and signature for binary stability.

## case03_compat_addition — additive symbol

```c
/* lib v1 */
int api_do_work(void);

/* lib v2 */
int api_do_work(void);
int api_get_version(void); /* new */
```

```c
/* old consumer */
extern int api_do_work(void);
```

Adding a new symbol usually does not break old consumers because they do not import it. Their original
imports are still present and resolvable, so load-time and call-time behavior remains compatible.
However, additions can still create long-term API surface commitments if they expose internals.

Best practice is to mark stability level, document lifecycle, and ensure added APIs do not leak private
layout types or unstable dependencies.

## case04_no_change — baseline control

```c
/* lib v1 */
int stable_add(int a, int b);
/* lib v2 */
int stable_add(int a, int b);
```

```c
/* consumer */
extern int stable_add(int, int);
```

No signature/layout/export changes means ABI should be identical. This case is important because it
validates the checker pipeline itself: if “no change” reports breakage, either comparison inputs are
mismatched (headers/flags/tool mode) or detection logic regressed.

Keep this case in CI as a guardrail against false positives and noisy policy failures.

## case05_soname — SONAME policy violation

```bash
# good release line
gcc -shared -Wl,-soname,libfoo.so.2 -o libfoo.so.2 foo.c

# bad release line
# ABI changed, but SONAME left as libfoo.so.1 (or omitted)
```

```bash
# consumer links to libfoo.so.1 in package metadata
ldd app
```

SONAME communicates ABI lineage to loaders and package tools. If ABI changes but SONAME does not, the
system can silently replace a previously compatible dependency with an incompatible one under the same
name. Failures then appear at runtime and are often misdiagnosed as environment issues.

Treat SONAME as mandatory release policy: incompatible ABI => SONAME bump + explicit migration notes.

## case06_visibility — accidental export leak

```c
/* intended internal helper */
int helper_internal(int x); /* accidentally exported in v1 */
```

```c
/* third-party consumer (undesired) */
extern int helper_internal(int);
```

When internal functions leak into exports, external users may start linking against them. That turns
implementation detail into de facto public ABI, even if undocumented. Removing or changing it later
breaks those consumers and locks your internals.

Use hidden visibility by default (`-fvisibility=hidden`) and explicitly export only supported public
entry points with an API macro.

## case07_struct_layout — public struct layout drift

```c
/* v1 */
struct Config { int a; int b; };

/* v2 */
struct Config { int a; long b; };
```

```c
/* consumer compiled with v1 header */
struct Config c = {1, 2};
lib_use_config(&c);
```

Public struct layout is ABI: field offsets, total size, and alignment are baked into consumer codegen.
If the library now reads `b` at a different offset/width, old callers pass memory in an outdated shape.
That can corrupt adjacent memory or produce nonsense values, especially across FFI boundaries.

Avoid by freezing public structs or hiding representation behind opaque handles and accessor functions.

## case08_enum_value_change — semantic wire break

```c
/* v1 */
enum Mode { MODE_OFF = 0, MODE_ON = 1 };

/* v2 */
enum Mode { MODE_OFF = 1, MODE_ON = 2 };
```

```c
/* consumer persisted old value 1 meaning MODE_ON */
write_mode_to_disk(MODE_ON);
```

Enum values are often protocol constants. Reassigning numbers changes semantics for persisted state,
network payloads, and cross-service interoperability. The program may still compile and run, but logic
silently diverges because “same name” maps to different integer meaning.

Never renumber released enum constants; append new values only.

## case09_cpp_vtable — virtual dispatch ABI break

```cpp
// v1
struct I {
  virtual void a();
  virtual void b();
};

// v2
struct I {
  virtual void b();
  virtual void a();
};
```

```cpp
// consumer compiled with v1 expectations
I* p = get_iface();
p->a();
```

Vtable slot ordering and signature thunks are part of C++ ABI. Reordering methods changes slot-to-
method mapping; old binaries may call the wrong function through the same call site. This yields
catastrophic semantic corruption without obvious linker errors.

Freeze virtual interface ABI or introduce a new interface version (`I2`) instead of mutating v1.

## case10_return_type — return ABI mismatch

```c
/* v1 */
int get_count(void);

/* v2 */
long get_count(void);
```

```c
/* consumer */
extern int get_count(void);
int x = get_count();
```

Return type impacts register usage and value interpretation at call boundary. Old caller expects `int`,
new callee returns `long`; truncation/sign mismatch can occur and behavior becomes target-dependent.
Source-level compatibility after rebuild does not protect already deployed binaries.

Preserve old symbol and add `get_count_v2` with new type.

## case11_global_var_type — exported global contract changed

```c
/* v1 */
extern int g_state;

/* v2 */
extern long g_state;
```

```c
/* consumer */
extern int g_state;
int snapshot = g_state;
```

Global variables are ABI surface. Consumer load/store width and relocation assumptions are compiled in.
Changing variable type can cause partial writes/reads or neighboring memory corruption.

Prefer getter/setter API and keep existing exported globals immutable in type and semantics.

## case12_function_removed — hard symbol break

```c
/* v1 */
int run_task(int id);

/* v2 */
/* removed */
```

```c
/* consumer */
extern int run_task(int);
```

This is equivalent to case01 at policy level: import remains in old consumer binary, export is gone in
new library, loader fails. Runtime outage risk is high because users can hit breakage simply by package
upgrade, without changing their code.

Use deprecation windows and SONAME-major removal policy.

## case13_symbol_versioning — version-script regression

```map
# v1
LIBFOO_1.0 { global: api_*; local: *; };

# v2 (bad)
# script removed / version tags changed incompatibly
```

```bash
readelf --version-info libfoo.so
```

Symbol version tags disambiguate ABI generations and improve compatibility in mixed environments.
Regressing the map can cause incorrect symbol binding across distro backports or plugin ecosystems.
Failures may be subtle and environment-specific, making them expensive to debug.

Keep version scripts under strict CI checks and treat changes as ABI governance events.

## case14_cpp_class_size — object layout size break

```cpp
// v1
class Obj { int x; };

// v2
class Obj { int x; int y; };
```

```cpp
// consumer stack/heap allocation assumes v1 sizeof(Obj)
Obj o;
lib_accept_obj(&o);
```

For public classes, object size and field offsets are ABI. If a newer library expects larger layout,
old allocations can be too small, causing writes beyond boundaries. Crashes may appear far from origin.

Use Pimpl to keep public object footprint stable.

## case15_noexcept_change — source-level contract change (COMPATIBLE)

```cpp
// v1
int process() noexcept;

// v2
int process();
```

```cpp
// consumer generic code assumes noexcept contract
static_assert(noexcept(process()));
```

**abicheck verdict: COMPATIBLE.** The Itanium ABI mangled name does not change when `noexcept` is
added or removed — the same symbol resolves at load time and existing binaries continue to call the
function correctly. This is a **source-level concern**, not a binary ABI break.

However, `noexcept` does participate in C++17 function type semantics (P0012R1), so function pointer
types may mismatch in template code. Additionally, code compiled with v1 headers may omit exception
landing pads, so if v2's function throws, `std::terminate` is called. These are behavioral/contract
concerns that should be reviewed, but they do not prevent already-compiled binaries from loading or
calling the function.

Treat `noexcept` as stable API commitment for source compatibility; evolve via new API version.

## case16_inline_to_non_inline — ODR/export behavior drift

```cpp
// v1 header
inline int mul2(int x) { return x * 2; }

// v2 header
int mul2(int x); // now out-of-line
```

```cpp
// old TU inlined old body; new TU links to symbol
```

Inline policy changes can create mixed semantics across translation units built at different times.
Some code paths embed old logic, others call new shared implementation. Depending on transition,
dynamic symbol set can also appear/disappear unexpectedly.

Keep public inline behavior stable, or version the API explicitly.

## case17_template_abi — instantiated layout mismatch

```cpp
// v1
template<class T> struct Box { T v; int tag; };

// v2
template<class T> struct Box { T v; long tag; };
```

```cpp
// module A and B compiled against different headers exchange Box<int>
```

Templates instantiate in user code, so layout changes propagate into every downstream build. Different
modules can disagree on representation for the same nominal type, causing corruption during boundary
crossing and serialization.

Avoid exposing unstable templates in ABI boundaries; provide non-template stable façade.

## case18_dependency_leak — transitive ABI dependency exposure

```cpp
// public API header
#include <thirdparty/task_arena.hpp>
struct ApiCfg { thirdparty::Arena arena; };
```

```cpp
// consumer and library built against different third-party versions
```

If public API embeds third-party types, your ABI is now coupled to another project's layout/versioning.
A third-party upgrade can break your consumers even when your own symbols are unchanged. This is a
common enterprise break pattern during distro refreshes.

Hide third-party types behind opaque wrappers and stable project-owned DTOs.

## case19_enum_member_removed — removed semantic state

```c
/* v1 */
enum Level { LOW=0, MED=1, HIGH=2 };

/* v2 */
enum Level { LOW=0, HIGH=2 };
```

```c
// old data contains MED=1
```

Removing enum members invalidates historical persisted/protocol values and old branch logic.
Runtime may still proceed, but state decoding becomes incomplete and behavior undefined by policy.

Keep historical values, deprecate in docs, and map legacy meaning deliberately.

## case20_enum_member_value_changed — value remap incompatibility

```c
/* v1 */
enum Status { OK=0, FAIL=1 };

/* v2 */
enum Status { OK=1, FAIL=2 };
```

```c
// remote peer sends 1 expecting FAIL, receiver treats as OK in changed mapping
```

Numeric remapping is effectively protocol rewrite without version negotiation. Old/new components can
exchange the same integer and interpret opposite semantics.

Never reassign released numbers; add new constants and introduce explicit protocol versioning.

## case21_method_became_static — member ABI identity changed

```cpp
// v1
struct S { int calc(int x) const; };

// v2
struct S { static int calc(int x); };
```

```cpp
// old consumer emits member call ABI with implicit this
S s; s.calc(7);
```

Static and instance methods have different mangling and call conventions (presence of implicit `this`).
Changing in place replaces one ABI endpoint with another. Old binaries fail to bind or invoke correctly.

Add static helper under new name; preserve old member API.

## case22_method_const_changed — mangled symbol changed

```cpp
// v1
struct S { int size() const; };

// v2
struct S { int size(); };
```

```cpp
// consumer compiled against const-qualified member symbol
```

Const qualification is part of C++ member function type and symbol identity. Changing it replaces the
old mangled symbol; existing binaries expecting const-qualified entry point cannot resolve new one.
It may also alter overload behavior for rebuilt source consumers.

Keep old method and add overload/new API variant.

## case23_pure_virtual_added — existing method made pure virtual

```cpp
// v1
class Processor { virtual void process(); };

// v2
class Processor { virtual void process() = 0; };
```

```cpp
// consumer compiled against v1 (concrete class)
Processor* p = new Processor();
p->process();  // calls concrete implementation
```

Making an existing concrete virtual method pure virtual replaces the vtable entry for that method
with the pure-call handler (`__cxa_pure_virtual`). Old binaries that call `process()` via vtable
dispatch now hit the handler and abort. Additionally, `Processor` becomes abstract — source-level
rebuilds fail to compile `new Processor()`.

Create `Processor2` as the new abstract interface and keep the original class frozen.

## case24_union_field_removed — representation set reduced

```c
/* v1 */
union Value { int i; float f; };

/* v2 */
union Value { int i; };
```

```c
// consumer stores float path
union Value v; v.f = 1.5f;
lib_consume(v);
```

Union fields define valid interpretations of shared storage. Removing one field removes a supported
representation and can invalidate persisted/exchanged data and branch logic relying on that variant.
Even if size stays constant, semantic compatibility is broken.

Prefer versioned replacement unions/structs plus explicit conversion rules.

---

## Compatible/warning changes (not binary ABI breaks)

The following changes are detected and reported by abicheck but classified as
**COMPATIBLE** — they are important for awareness but do not cause binary linkage
or layout failures when swapping a shared library between two releases.

### Enum member added

```c
/* v1 */
enum Color { RED = 0, GREEN = 1, BLUE = 2 };

/* v2 */
enum Color { RED = 0, GREEN = 1, BLUE = 2, YELLOW = 3 };
```

```c
/* consumer compiled against v1 */
switch (get_color()) {
    case RED: ...; case GREEN: ...; case BLUE: ...;
    /* YELLOW not handled — but binary still works correctly */
}
```

Adding a new enum member does not change the numeric values of existing members. Already-compiled
binaries continue to pass and receive the same integer values. The concern is source-level: switch
statements may not handle the new value, and sentinel patterns like `COLOR_COUNT` may need updating.
If adding the member shifts existing values, that is a separate change caught by
`ENUM_MEMBER_VALUE_CHANGED` (which IS breaking).

### Union field added

```c
/* v1 */
union Value { int i; float f; };

/* v2 */
union Value { int i; float f; double d; };
```

All union fields share offset 0, so adding a new field does not change how existing fields are
accessed at offset level. However, if the new field is larger than existing members (as in this
case: `double` is 8 bytes vs `int`/`float` at 4 bytes), the union's `sizeof` grows — triggering
`TYPE_SIZE_CHANGED` and a **BREAKING** verdict. Old callers that stack-allocate the union
under-allocate memory when running against the v2 library.

### noexcept added or removed

See [case15_noexcept_change](#case15_noexcept_change-source-level-contract-change-compatible) above.

### GLOBAL→WEAK symbol binding

```text
v1: foo  GLOBAL  DEFAULT  → strong binding
v2: foo  WEAK    DEFAULT  → overridable binding
```

A WEAK symbol is still exported and resolvable by the dynamic linker. Existing binaries that linked
against the GLOBAL version will find and use the (now WEAK) symbol without any change in behavior.
The only difference is that the symbol can now be overridden by another definition — an interposition
concern, not a compatibility break.

### GNU IFUNC introduced or removed

Converting a regular function to GNU IFUNC (indirect function) or back is transparent to callers.
The PLT/GOT resolution mechanism handles indirect dispatch automatically. This is an implementation
optimization (e.g., CPU-specific dispatch) that does not change the calling convention or symbol
contract visible to consumers.

### Note: borderline checks classified as BREAKING

Some changes are borderline but classified as **BREAKING** because they can cause runtime failures:

- **ELF `st_size` changed** — in ELF-only mode (no headers/DWARF), may be the sole signal for
  vtable growth or variable type changes. Classified BREAKING to avoid false negatives.
- **New dependency version requirement** (e.g., `GLIBC_2.34`) — the library fails to load on
  runtimes lacking the version. Hard runtime failure.
- **Typeinfo/vtable visibility changed** — cross-DSO `dynamic_cast` and C++ exception matching
  can fail at runtime.
- **Variable const qualifier added/removed** — adding `const` moves to `.rodata`; existing writes
  cause SIGSEGV. Removing `const` breaks ODR/inlining assumptions.

---

## Global compatibility rules

1. Treat public headers as ABI contracts.
2. Use SONAME + symbol versioning + visibility policy on every release.
3. Prefer opaque handles/Pimpl to avoid exposing mutable layouts.
4. Evolve with additive/versioned APIs, not in-place mutation.
5. Keep these cases in CI as mandatory ABI regression checks.
