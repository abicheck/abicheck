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

#include "generated/config.hpp"  // generated public header → GENERATED origin

namespace demo {

typedef int handle_t;
using size_type = unsigned long;

enum class Color { Red, Green, Blue };

struct Point {
  int x;
  int y;
};

constexpr int kMaxItems = 128;
constexpr int kComputed = 4 * 32 + 1;  // non-literal init -> subtree hash

int add(int a, int b = 1);             // default arg (literal int)
bool toggle(bool on = true);           // default arg (literal bool)

inline int square(int n) { return n * n; }  // inline body -> body hash

template <class T>
T identity(T v) { return v; }               // function template -> body hash

template <class T>
struct Box {                                 // class template -> body hash
  T value;
  T get() const { return value; }            // NOT emitted (no descent)
};

class Widget {
public:
  Widget();
  int area() const;

private:
  struct Impl {          // private nested type; its public members stay hidden
   public:
    void run();          // must be dropped by both producers
  };
  int w_;
};

}  // namespace demo
