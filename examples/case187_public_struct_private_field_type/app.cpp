#include "v1.h"

int main() {
    demo::Public p{42, demo::Meta{0}, nullptr};
    return demo::use_public(p) == 42 ? 0 : 1;
}
