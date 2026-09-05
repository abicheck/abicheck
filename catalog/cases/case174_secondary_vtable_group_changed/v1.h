#pragma once

// v1: Derived inherits from two bases. Base1 is polymorphic (owns a vtable)
// and becomes the *primary* base; Base2 is an ordinary, non-polymorphic
// helper class contributing only a plain (non-virtual) member function.
// Because Base2 is not polymorphic, it needs no vtable group of its own —
// Derived's dispatch surface is entirely Base1's.
struct Base1 {
    virtual int f1();
    virtual ~Base1();
};

struct Base2 {
    int helper();
};

// Derived's own declaration — its base list — is identical in v1 and v2.
struct Derived : Base1, Base2 {};

extern "C" Derived* make_derived();
extern "C" unsigned long derived_size();
