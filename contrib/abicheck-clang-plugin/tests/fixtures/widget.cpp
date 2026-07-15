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

// ADR-038 C.6 PR1b regression fixture: an overloaded callee resolved through
// clang's compact `referencedDecl` (no `mangledName` on the stub -- verified
// against real Clang 17/18 JSON AST dumps) must not collapse `overload(int)`
// and `overload(double)` onto one bare-name `source_edges` endpoint. Both
// producers (plugin: live FunctionDecl*; clang backend replay: the id-index
// fix in call_graph.py) must resolve these to their distinct mangled
// identities so the two `source_edges` sets stay equivalent.
int overload(int x) { return x; }
double overload(double x) { return x; }
int callOverloadInt() { return overload(1); }
double callOverloadDouble() { return overload(1.0); }

}  // namespace demo
