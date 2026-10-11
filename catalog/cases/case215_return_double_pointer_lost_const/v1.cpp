#include "v1.h"

static const char *names[] = {"alpha", "beta", nullptr};

const char **mylib::get_names() { return names; }
