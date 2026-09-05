/* case32 app: Demonstrate parameter default value changes (C++).
 *
 * Compiled against v1.hpp where:
 *   connect(int timeout = 30)
 *   configure(bool verbose = true, int retries = 3)
 *   disconnect(int code)  — no default
 *
 * v2.hpp changes:
 *   connect(int timeout = 60)         — default changed
 *   configure(bool verbose, int retries = 5) — verbose lost default
 *   disconnect(int code = 0)          — default added
 *
 * C++ default arguments are resolved at the CALL SITE by the compiler.
 * The defaults are baked into the caller's binary, not the library.
 * So swapping v2.so changes nothing — the app still passes the v1 defaults.
 *
 * This is NOT a binary ABI break. It is a source-level concern only.
 */
#include "v1.hpp"
#include <cstdio>

int main() {
    Connection conn;

    std::printf("Parameter defaults demo (compiled against v1.hpp):\n\n");

    /* connect() with default timeout=30 — baked into caller as connect(30) */
    std::printf("Calling connect() with default timeout:\n");
    std::printf("  Compiled as connect(30) from v1 header\n");
    conn.connect();
    std::printf("  OK — v2 default is 60, but caller already passed 30\n\n");

    /* connect with explicit value — same in both versions */
    std::printf("Calling connect(45) with explicit timeout:\n");
    conn.connect(45);
    std::printf("  OK — explicit args are unaffected\n\n");

    /* configure() with both defaults — compiled as configure(true, 3) */
    std::printf("Calling configure() with defaults:\n");
    std::printf("  Compiled as configure(true, 3) from v1 header\n");
    conn.configure();
    std::printf("  OK — v2 removed verbose default, but caller already passed true\n\n");

    /* configure with one default — compiled as configure(false, 3) */
    std::printf("Calling configure(false) with one default:\n");
    std::printf("  Compiled as configure(false, 3) from v1 header\n");
    conn.configure(false);
    std::printf("  OK — retries default was 3 (v1), baked into binary\n\n");

    /* disconnect requires explicit code in v1 — v2 adds default=0 */
    std::printf("Calling disconnect(1):\n");
    conn.disconnect(1);
    std::printf("  OK — disconnect requires explicit arg in v1\n");
    std::printf("  v2 adds default=0, but that only helps NEW callers\n\n");

    std::printf("Summary:\n");
    std::printf("  - Default values are resolved at compile time in the CALLER\n");
    std::printf("  - connect()    -> compiled as connect(30)         [v2 default: 60]\n");
    std::printf("  - configure()  -> compiled as configure(true, 3)  [v2: no default for verbose]\n");
    std::printf("  - disconnect() -> must pass explicit arg           [v2 adds default=0]\n");
    std::printf("  - Binary is 100%% compatible: same mangled symbols, same calling convention\n");
    std::printf("  - NO_CHANGE at binary ABI level\n");

    return 0;
}
