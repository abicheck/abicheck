#include "api.hpp"
#include <cstdio>
#include <typeinfo>

static int ctor_phase, ctor_address, cleanups;
static void observe(L* left, V* root) {
    ctor_address = static_cast<V*>(left) == root;
    ctor_phase = root->phase();
}
struct Guard { ~Guard() {++cleanups;} };
static void unwind() {Guard g; raise_error();}
int main() {
    L* left = create(observe);
    V* root = as_v(left);
    R* right = dynamic_cast<R*>(left);
    int handler = 0;
    try {unwind();} catch (const Error&) {handler = 1;} catch (...) {handler = 2;}
    std::printf("%d,%d,%d,%d,%d,%d,%d,%d\n", ctor_phase, ctor_address, root->phase(),
        static_cast<V*>(left) == root, typeid(*left)==typeid(D), right ? right->right() : 0,
        handler, cleanups);
}
