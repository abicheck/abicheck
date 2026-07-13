#include <cstdio>
#include <dlfcn.h>

// This app links against nothing from v1/v2 at compile time -- it loads
// whichever library is present as "./libv1.so" at runtime and looks up
// symbols purely by name. That is deliberate: most consumers only ever call
// a stable `extern "C"` wrapper (pick_by_policy), but this demo instead
// reaches for the raw, compiler-generated lambda invoker symbol -- exactly
// the kind of direct dependency the finding warns is fragile. Its exact
// spelling is an implementation detail of *this* compiler, *this* standard
// library, and *this* translation unit's declaration order, not a contract
// anyone declared.
typedef int (*two_int_fn)(int, int);

int main() {
    void *handle = dlopen("./libv1.so", RTLD_NOW);
    if (!handle) {
        printf("dlopen FAILED: %s\n", dlerror());
        return 1;
    }

    auto pick_larger = (two_int_fn)dlsym(handle, "pick_larger");
    printf("pick_larger(3, 7) = %d\n", pick_larger ? pick_larger(3, 7) : -1);

    const char *raw_symbol = "_ZN10descendingMUliiE_4_FUNEii";
    void *sym = dlsym(handle, raw_symbol);
    if (!sym) {
        printf("direct lookup of %s: not present in this build "
               "(expected against v1; v2 introduces it)\n", raw_symbol);
        return 0;
    }
    printf("direct lookup of %s succeeded -- but do not rely on this exact "
           "name surviving a rebuild.\n", raw_symbol);
    return 0;
}
