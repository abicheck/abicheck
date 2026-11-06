/* Witness: an unchanged old consumer relies on the documented -1 sentinel
   and has no try/catch, exactly as v1's contract allowed.
     v1: parse_port("http") == -1, falls back to 8080   -> exit 0
     v2: std::invalid_argument escapes main -> std::terminate -> abort */
#include "v1.h"

#include <cstdio>

int main() {
    int port = cfg::parse_port("http");
    if (port < 0) port = 8080;
    std::printf("port = %d\n", port);
    return port == 8080 ? 0 : 1;
}
