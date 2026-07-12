// Conformance fixture for the abicheck Clang facts plugin (ADR-038 C.6).
//
// Exercises every entity kind the plugin and the clang backend must agree on:
// records, enums, typedefs/aliases, an inline body (subtree hash), function and
// class templates (subtree hash), constexpr literals and a *computed* constexpr
// (subtree hash), default arguments (literal int + bool), and public/private
// visibility (a public member of a private nested class must be dropped).
#pragma once

// Macro values are always compared leniently (warn, not fail) in the C.6 test
// (ADR-038 C.7). Object-like macros happen to reconstruct identically to the
// clang `-E -dD` backend; the function-like WIDGET_SCALE exercises the known
// operator-adjacent spacing difference that the lenient comparison tolerates.
#define WIDGET_VERSION 3
#define WIDGET_ENABLED 1
#define WIDGET_SCALE(x) ((x) * 2)
#define WIDGET_LOG(fmt, args...) fmt  // GNU named variadic → params keep `args...`

#include "generated/config.hpp"  // generated public header → GENERATED origin
#include "guarded.hpp"  // project-prefixed include guard — see guarded.hpp

namespace demo {

typedef int handle_t;
using size_type = unsigned long;
typedef const char *cstring_t;  // sugared/pointer target: underlying comes from
                                // clang's JSON qualType, not the pretty-printer

enum class Color { Red, Green, Blue };
enum class EmptyEnum {};  // empty enum — clang backend skips it (no `inner`)
struct EmptyStruct {};    // empty struct — has implicit members, so kept

struct Point {
  int x;
  int y;
};

constexpr int kMaxItems = 128;
constexpr int kComputed = 4 * 32 + 1;  // non-literal init -> subtree hash

// Pruned-parser stress (perf opt regression guard): the plugin hashes subtrees
// by parsing clang's JSON keeping only hash-relevant keys and skipping the rest.
// A string literal with quotes/backslashes exercises the parser's string-escape
// skipping (its `value` is a KEPT scalar), and a deeply nested template argument
// exercises balanced object/array skipping — a bug in either would change the
// subtree hash and break the C.6 differential gate. Non-literal init -> the value
// is a subtree hash, so the whole escaped-string AST flows through the parser.
constexpr const char *kEscaped = "a\"b\\c\n\t}]{[";  // escaped quote/backslash/braces
template <class A, class B>
struct Pair2 { A first; B second; };
inline int nestedTemplateSink() {
  Pair2<Pair2<int, char>, Pair2<double, Pair2<int, int>>> p{};
  return p.first.first;
}

// External-linkage data variables — become exported OBJECT symbols, so both
// producers emit them as `variable` entities (ADR-030 D4). A namespace-scope
// `static` (internal linkage) and a block-scope local are NOT variables and are
// dropped by both; a static data member IS external and is kept.
int gCounter;                          // namespace global -> variable
extern int gShared;                    // extern global -> variable
static int gInternal = 0;              // static: internal linkage -> dropped by both
const int gConstInternal = 3;          // namespace const w/o extern: internal -> dropped

int add(int a, int b = 1);             // default arg (literal int)
bool toggle(bool on = true);           // default arg (literal bool)

inline int square(int n) { return n * n; }  // inline body -> body hash

// A PUBLIC inline function whose body declares a local type. Both producers
// descend into an accessible body, so both emit the local record — and both
// name it `demo::Scaled` (clang.py's scope stack does not push function
// scopes). The plugin must match via scopedName() rather than emitting
// `demo::scaledResult()::Scaled` (Codex review, line ~1013).
inline int scaledResult(int v) {
  struct Scaled { int out; };
  Scaled s{v * 2};
  return s.out;
}

template <class T>
T identity(T v) { return v; }               // function template -> body hash

template <>
inline int identity(int v) { return v + 1; }  // explicit specialization (callable)

template <class T>
using Ptr = T *;                            // alias template -> typedef `Ptr`

template <class T>
struct Box {                                 // class template -> body hash
  T value;
  T get() const { return value; }            // member pattern -> Box<T>::get
};

class Widget {
public:
  Widget();
  int area() const;
  static int sInstances;   // static data member -> external OBJECT -> variable

private:
  struct Impl {          // private nested type; its public members stay hidden
   public:
    void run();          // must be dropped by both producers
  };
  // A private method with an inline body that declares a body-local type. The
  // whole subtree of an inaccessible function is hidden, so neither producer
  // may emit `Scratch` (regression guard: an isAccessible() walk that stopped
  // at the FunctionDecl context would have leaked it in as public).
  int compute() const {
    struct Scratch { int lo; int hi; };
    Scratch s{w_, w_};
    return s.hi - s.lo;
  }
  int w_;
};

inline int withLocalConst() {
  constexpr int kLocal = 7;   // block-scope constexpr in a PUBLIC inline fn
  return kLocal;
}

}  // namespace demo

// extern "C" out-of-line def: mangled name is suppressed, so identity falls
// back to qualified_name#sig — the case where lexical vs semantic scope matters.
namespace ec { extern "C" int cfn(int); }
extern "C" inline int ec::cfn(int x) { return x; }

// A type nested in an explicit class-template specialization. clang's JSON kind
// for the specialization is ClassTemplateSpecializationDecl (not a scope node in
// the backend), so the nested record is named `spec::Nested`, not
// `spec::Q::Nested` — scopedName() must exclude the specialization from the
// scope stack even though it derives from CXXRecordDecl (regression guard).
namespace spec {
template <class T>
struct Q { int a; };
template <>
struct Q<int> {
  struct Nested { int z; };
  int b;
};
}  // namespace spec

// A `#line` directive (as generated/amalgamated public headers emit) remaps the
// PRESUMED filename; classification must use the PHYSICAL file
// (UseLineDirectives=false) like the backend, or this decl — and everything
// after it — would be dropped out of the public roots. Kept last so the remap
// affects nothing else (regression guard).
namespace ld {
#line 900 "virtual_amalgamated.hpp"
int remapped_public(int);
}  // namespace ld
