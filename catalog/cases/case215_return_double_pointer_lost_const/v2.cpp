#include "v2.h"

static char alpha[] = "alpha";
static char beta[] = "beta";
static char *names[] = {alpha, beta, nullptr};

char **mylib::get_names() { return names; }
