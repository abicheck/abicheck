// Translation unit compiled by both producers in the C.6 conformance test.
// The out-of-line definitions live in this source file (not under include/), so
// both the plugin and the clang backend classify them non-public and drop them;
// only the public-header declarations in widget.hpp are compared.
#include "widget.hpp"

namespace demo {

Widget::Widget() : w_(0) {}
int Widget::area() const { return w_ * w_; }
int add(int a, int b) { return a + b; }
bool toggle(bool on) { return !on; }
void Widget::Impl::run() {}

}  // namespace demo
