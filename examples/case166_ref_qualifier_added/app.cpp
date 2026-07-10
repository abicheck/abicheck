/* DEMO: app compiled against v1 references _ZN14MessageBuilder3strEv.
   v2 adds an lvalue ref-qualifier to str(), which renames the symbol to
   _ZNR14MessageBuilder3strEv — the dynamic linker aborts the app at
   startup with "undefined symbol". */
#include "v1.h"
#include <cstdio>
#include <cstring>

int main() {
    MessageBuilder b;
    b.append("status=").append("ok");
    const char* s = b.str();
    std::printf("message = %s (expected status=ok)\n", s);
    return std::strcmp(s, "status=ok") != 0;
}
