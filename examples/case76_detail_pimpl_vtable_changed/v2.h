// case76 v2 — detail::algorithm_iface gains a new virtual method *in the
// middle* of its vtable order. Every consumer of the public `svm_algorithm`
// (or any other class deriving from detail::algorithm_iface) gets vtable
// slot shifts: callers compiled against v1 dispatching to "status()"
// actually call the new "progress()" at runtime.
#pragma once

namespace mylib {
namespace detail {

class algorithm_iface {
public:
    // See v1 — the out-of-line destructor is the *key function* that
    // forces the vtable to be emitted as a real exported symbol on
    // every platform.
    virtual ~algorithm_iface();
    virtual int run() = 0;
    virtual int progress() const;    // NEW virtual inserted before status()
    virtual int status() const = 0;
};

} // namespace detail

class svm_algorithm : public detail::algorithm_iface {
public:
    svm_algorithm();
    int run() override;
    int progress() const override;
    int status() const override;
private:
    int state_;
};

extern "C" detail::algorithm_iface* mylib_make_svm();
extern "C" void mylib_free_algo(detail::algorithm_iface*);

} // namespace mylib
