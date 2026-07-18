#include "v1.h"

int main() {
    demo::PublicHandle h{{1}, 42};
    return demo::use_handle(h) == 42 ? 0 : 1;
}
