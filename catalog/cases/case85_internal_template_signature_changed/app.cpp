// case113 — consumer instantiates sum_range<float>, which inlines
// __detail::walk<float> into the consumer's symbol table. Against v2
// that internal helper instantiation is gone.
#include "v1.h"

int main() {
    int a[3] = {1, 2, 3};
    int s_i = lib::sum_range(a, a + 3);

    float b[3] = {1.0f, 2.0f, 3.0f};
    float s_f = lib::sum_range(b, b + 3);

    return (s_i == 6 && s_f == 6.0f) ? 0 : 1;
}
