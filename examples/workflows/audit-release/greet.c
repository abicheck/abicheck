#include "greet.h"

#include <stdio.h>

static char buffer[256];

/* Not declared in include/greet.h, and not marked `static` or given hidden
 * ELF visibility -- an accidental export. It compiles and links fine, and
 * nobody who reads the public header would ever know it exists, but the
 * default visibility means it lands in the shared library's dynamic symbol
 * table anyway: any consumer can already `dlsym()` or link against it. */
void debug_dump(void) {
    fprintf(stderr, "greet: buffer=%s\n", buffer);
}

const char *greet(const char *name) {
    snprintf(buffer, sizeof(buffer), "Hello, %s!", name);
    return buffer;
}
