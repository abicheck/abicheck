#include "v1.h"

int main() {
    demo::Config configs[4];
    demo::fill_configs(configs, 4);
    int sum = 0;
    for (auto& c : configs) {
        sum += demo::use_config(c);
    }
    return sum == 6 ? 0 : 1;
}
