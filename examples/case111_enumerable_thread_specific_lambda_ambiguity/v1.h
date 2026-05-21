// case111 v1 — thread-local storage class with a single init constructor.
//
// Mirrors a historical oneTBB `enumerable_thread_specific<T>` shape:
// constructible with an explicit initial value. Consumers wrote
//
//     mylib::enumerable_thread_specific<int> ets(42);
//
// unambiguously.
//
// NOTE: a real oneTBB-flavoured version would accept `std::function<int()>`
// as the lambda-init parameter, but we use a plain function-pointer typedef
// here. Pulling in `<functional>` from libstdc++ 13 breaks castxml dumping
// (clang rejects `__attribute__((__assume__(...)))` inside `stl_bvector.h`),
// which would prevent abicheck's integration tests from ever running on
// this case. A function-pointer overload is sufficient to demonstrate the
// overload-ambiguity risk at consumer call sites.
#pragma once

namespace mylib {

class enumerable_thread_specific {
public:
    // Initial-value constructor.
    explicit enumerable_thread_specific(int initial_value);

    int local() const;

private:
    int value_;
};

} // namespace mylib
