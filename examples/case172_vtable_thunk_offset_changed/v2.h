#pragma once

// v2: Base1 gains a second data member (`double y`). Base1's own size grows
// from 16 to 24 bytes, which pushes Base2's subobject offset inside Derived
// from 16 to 24 as well. Derived's *method set* is completely unchanged —
// still f1(), f2() (overridden), and the destructor — so the number of
// vtable slots (`_ZTV7Derived`'s size) does not change at all. What changes
// is purely the this-adjustment baked into f2()'s non-virtual thunk.
struct Base1 {
    int x;
    double y;      // <-- new field: grows Base1, shifts Base2's offset
    virtual int f1();
    virtual ~Base1();
};

struct Base2 {
    virtual int f2();
    virtual ~Base2();
};

struct Derived : Base1, Base2 {
    int f2() override;
};

extern "C" Derived* make_derived();
