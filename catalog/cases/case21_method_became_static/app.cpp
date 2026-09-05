#include "old/lib.h"
#include <cstdio>

/* App compiled against v1 header (instance method).
 * With v2 (method became static), symbol still resolves, but call ABI differs.
 * Here we demonstrate deterministic WRONG RESULT (not just potential crash).
 */
int main() {
    Widget w{};
    w.value = 41;

    int got = w.bar();
    std::printf("got=%d expected=42\n", got);

    if (got != 42) {
        std::printf("WRONG RESULT: method call contract changed (instance -> static)\n");
        return 1;
    }
    return 0;
}
