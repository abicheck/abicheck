// case105 v1 — a C++20 `concept` with a single requirement.
//
// `Addable` constrains a single type-parameter `T` to support `a + b`.
// The library exposes a constrained free function `sum<T>` whose
// template parameter list demands the concept. Consumers writing
// `mylib::sum(int(1), int(2))` succeed because `int` satisfies the
// concept.
//
// In v2, the maintainer tightens the concept: it now additionally
// requires `T()` (default-constructibility). The mangled name of the
// generated instantiation `sum<int>` is unchanged, so previously-
// compiled binaries keep linking — but consumer source that
// instantiates `sum<T>` against a type that fails the new requirement
// no longer compiles.
//
// This case is the prototypical "concept tightening" header-AST
// detection target on the roadmap.
#pragma once

// NOTE: we deliberately keep this file free of `<concepts>` so the
// example builds against castxml's bundled clang, whose libstdc++
// search path may not include the C++20 concepts header. The single-
// expression `requires` body is sufficient to demonstrate the
// tightening break.
namespace mylib {

template <typename T>
concept Addable = requires(T a, T b) {
    a + b;
};

template <Addable T>
T sum(T a, T b);

} // namespace mylib
