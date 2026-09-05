#include "v1.h"
#include <cstring>

/* extern "C" linkage comes from v1.h — no need to repeat it here */
int parse_config(const char *path) {
    /* Simplified: return 1 if path looks like a config file */
    return path && std::strlen(path) > 0;
}

int validate_config(const char *path) {
    return parse_config(path);
}
