#include "v2.h"
#include <cstring>

/* No extern "C" — C++ mangled names exported */
int parse_config(const char *path) {
    return path && std::strlen(path) > 0;
}

int validate_config(const char *path) {
    return parse_config(path);
}
