/* good.c — v2: Session has a new 'priority' field at the end.
   Because the struct is opaque (callers never sizeof or embed it),
   adding a field is safe — all allocation is done by the library. */
#include "lib.h"
#include <stdlib.h>
#include <string.h>

struct Session {
    char name[64];
    int timeout;
    int priority;   /* NEW — added at end */
};

Session* session_open(const char *name) {
    Session *s = calloc(1, sizeof(Session));
    if (!s) return NULL;
    if (name)
        strncpy(s->name, name, sizeof(s->name) - 1);
    s->timeout = 30;
    s->priority = 0;
    return s;
}

void session_close(Session *s) { free(s); }
const char* session_get_name(const Session *s) { return s->name; }
int session_get_timeout(const Session *s) { return s->timeout; }
int session_get_priority(const Session *s) { return s->priority; }
