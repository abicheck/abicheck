#include "old/lib.h"

int main() {
    const Widget w;
    w.get();   /* looks up _ZNK6Widget3getEv (const mangled name) */
    return 0;
}
