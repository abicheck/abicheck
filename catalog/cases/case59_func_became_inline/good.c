/* good.c — v2: functions are now inline in the header.
   This file is empty but needed so the shared library has
   at least one translation unit. We add a version symbol. */
#include "good.h"

int lib_version(void) { return 2; }
