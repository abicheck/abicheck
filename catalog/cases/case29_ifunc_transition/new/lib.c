#include "lib.h"

static int dispatch_generic(int x) { return x * 2; }

static int (*resolve_dispatch(void))(int) { return dispatch_generic; }

int dispatch(int x) __attribute__((ifunc("resolve_dispatch")));
