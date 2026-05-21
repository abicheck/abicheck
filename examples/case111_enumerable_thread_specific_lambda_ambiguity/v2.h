// case111 v2 — adds a lambda/initializer-style constructor overload.
//
// The new overload `enumerable_thread_specific(int_factory_t)` makes
// calls like `ets(42)` still resolve to the int-form, but generic
// callable arguments become ambiguous at consumer call sites.
//
// NOTE: see v1.h — we use `typedef int (*int_factory_t)()` instead of
// `std::function<int()>` to avoid pulling in libstdc++ 13's
// `<functional>` (which trips castxml). The overload-ambiguity story
// is the same either way.
#pragma once

namespace mylib {

typedef int (*int_factory_t)();

class enumerable_thread_specific {
public:
    explicit enumerable_thread_specific(int initial_value);
    // NEW overload — introduces ambiguity at consumer sites.
    explicit enumerable_thread_specific(int_factory_t init);

    int local() const;

private:
    int value_;
};

} // namespace mylib
