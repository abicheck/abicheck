#pragma once

// v1: Base1 carries one int data member ahead of Derived's second base,
// Base2. Base2's byte offset inside Derived is therefore fixed at
// sizeof(Base1) (16 bytes: 8-byte vptr + 4-byte int + 4 bytes padding).
struct Base1 {
    int x;
    virtual int f1();
    virtual ~Base1();
};

struct Base2 {
    virtual int f2();
    virtual ~Base2();
};

// Derived overrides f2(), a method it inherits from its *non-primary* base
// (Base1 is primary; Base2 is secondary). The Itanium ABI compiles this
// override behind a "non-virtual this-adjusting" thunk (`_ZThn..._`) that
// subtracts Base2's offset from `this` before jumping to the real
// implementation, so a call through a Derived* (which sees `this` as the
// complete object) and a call through a Base2* (which sees `this` as the
// Base2 subobject) both land in the same code correctly adjusted.
struct Derived : Base1, Base2 {
    int f2() override;
};

extern "C" Derived* make_derived();
