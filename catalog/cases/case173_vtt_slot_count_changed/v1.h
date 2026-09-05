#pragma once

// v1: a classic virtual-inheritance diamond. Left and Right each hold a
// *virtual* base Base, so Diamond has exactly one Base subobject shared
// between them. Constructing a Diamond therefore needs a VTT ("virtual
// table table") — an array of construction sub-vtables that lets each
// base class's own constructor see a temporarily-correct view of the
// not-yet-fully-constructed object before Diamond's constructor finishes
// wiring up the final virtual-base offsets.
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

struct Diamond : Left, Right {};

extern "C" Diamond* make_diamond();
extern "C" unsigned long diamond_size();
