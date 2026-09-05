#pragma once

// v2: Diamond gains a third virtual-inheritance leg, Mixin. Diamond's
// construction now needs one MORE sub-vtable (for Mixin's construction
// phase), growing the VTT. Note this is a genuinely bigger virtual-base
// shape change — it also grows Diamond's main vtable and RTTI (already
// covered by case142/case09) — but the VTT is what specifically proves the
// *construction-time* scaffolding changed, a fact those other signals do
// not carry on their own (see README).
struct Base {
    virtual int id();
    virtual ~Base();
};

struct Left : virtual Base {
    virtual int left();
};

struct Right : virtual Base {
    virtual int right();
};

struct Mixin : virtual Base {
    virtual int mixin();
};

struct Diamond : Left, Right, Mixin {};

extern "C" Diamond* make_diamond();
extern "C" unsigned long diamond_size();
