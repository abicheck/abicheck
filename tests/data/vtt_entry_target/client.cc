#include "api.hpp"
#include <cstdio>

static int ctor_phase;
static void observe(L*, V* root) { ctor_phase = root->phase(); }
int main() {
    L* left = create(observe);
    // construction-time dispatch, then dispatch on the complete object
    std::printf("%d,%d\n", ctor_phase, as_v(left)->phase());
}
