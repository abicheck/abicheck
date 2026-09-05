#pragma once

// v2: Base2's OWN declaration gains a virtual method — it is now
// polymorphic. Derived's declaration (`struct Derived : Base1, Base2 {}`,
// unchanged, below) never mentions this: nothing about Derived's own base
// list or members is any different from v1. But the Itanium ABI now must
// give Derived a *secondary* vtable group for Base2 (Base1 stays primary),
// so dispatch through a Base2* obtained from a Derived object works.
struct Base1 {
    virtual int f1();
    virtual ~Base1();
};

struct Base2 {
    virtual int helper();   // <- now polymorphic; Base2's own change only
    virtual ~Base2();
};

// Byte-for-byte identical declaration to v1's.
struct Derived : Base1, Base2 {};

extern "C" Derived* make_derived();
extern "C" unsigned long derived_size();
