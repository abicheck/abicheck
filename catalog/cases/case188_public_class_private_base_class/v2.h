// case188 v2 — demo::PublicHandle gains a second base class,
// demo::detail::InternalBase, declared only in an internal, non-public
// header. Adding a base class changes the object's layout — a real,
// binary-visible break in its own right (existing detectors correctly flag
// it) — and public_api_internal_dependency_added fires alongside it,
// specifically identifying that the *new* dependency reaches an internal
// type: the risk survives independent of whatever the layout-level finding
// says, and localizes exactly where the new coupling to internal code came
// from.
#pragma once

#include "detail_private.h"

namespace demo {

struct Base {
    int b;
};

struct PublicHandle : Base, detail::InternalBase {
    int x;
};

int use_handle(const PublicHandle& h);

} // namespace demo
