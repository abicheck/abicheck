/* Witness: an unchanged old consumer converts the documented seconds into a
   deadline and checks that it stays under one hour.
     v1: 30 s                 -> exit 0
     v2: 30000 "s" (~8.3 h)   -> exit 1 */
#include "v1.h"

#include <cstdio>

int main() {
    long seconds = net::session_timeout();
    std::printf("session timeout = %ld s\n", seconds);
    if (seconds > 3600) {
        std::printf("WRONG RESULT: timeout is no longer in seconds\n");
        return 1;
    }
    return 0;
}
