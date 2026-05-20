// case76 v1 — public class inherits from a detail:: polymorphic base.
//
// Public consumers call virtual methods through the public class. The
// vtable layout is determined by the detail:: base. Adding a virtual
// method to the detail:: base reshuffles the vtable, breaking already-
// compiled callers that dispatch by index.
#pragma once

namespace mylib {
namespace detail {

class algorithm_iface {
public:
    // Out-of-line destructor — gives this abstract class a *key function*
    // so the vtable / typeinfo symbols are emitted in v1's binary. Without
    // a key function, MSVC / Mach-O linkers may omit the vtable symbol
    // entirely (it would only appear in TUs that instantiate a derived
    // class), in which case the v1→v2 diff would look like the vtable was
    // *added* instead of *changed* — masking the real ABI break.
    virtual ~algorithm_iface();
    virtual int run() = 0;
    virtual int status() const = 0;
};

} // namespace detail

class svm_algorithm : public detail::algorithm_iface {
public:
    svm_algorithm();
    int run() override;
    int status() const override;
private:
    int state_;
};

extern "C" detail::algorithm_iface* mylib_make_svm();
extern "C" void mylib_free_algo(detail::algorithm_iface*);

} // namespace mylib
