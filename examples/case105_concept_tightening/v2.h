// case105 v2 — `Addable` gains an extra requirement.
//
// The concept now demands that `T` be default-constructible in addition
// to supporting `a + b`. Consumers that instantiated `sum<T>` against
// a type without a default constructor (e.g. a wrapper with only an
// int-arg constructor) fail to compile against v2 even though the
// previously-emitted instantiation `sum<int>` still links — the new
// constraint is satisfied for `int` but rejects more exotic types.
//
// abicheck cannot currently detect concept tightening end-to-end:
// castxml emits concept declarations as
//     <Unimplemented kind="Concept"/>
// with no body, no name, and no link to the constrained template.
// Closing this gap requires the header-AST capture path noted on the
// roadmap (a libclang-based extractor sitting alongside castxml).
//
// The case is preserved here as a regression fixture for whenever that
// path lands.
#pragma once

// NOTE: see v1.h — `<concepts>` is intentionally not included so the
// fixture builds under castxml's bundled clang.
namespace mylib {

template <typename T>
concept Addable = requires(T a, T b) {
    a + b;
    T();  // NEW requirement — default-constructibility tightening.
};

template <Addable T>
T sum(T a, T b);

} // namespace mylib
