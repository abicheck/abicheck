#include "v2.h"

namespace demo {

void fill_configs(Config* out, int n) {
    for (int i = 0; i < n; ++i) {
        out[i].value = i;
        out[i].raw = nullptr;
    }
}

int use_config(const Config& c) { return c.value; }

} // namespace demo
