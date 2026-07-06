/* bad.c — v1: Session has name and timeout fields. */
#include "bad.h"
#include <stdlib.h>
#include <string.h>

struct Session {
    char name[64];
    int timeout;
    int _reserved0;
};

Session* session_open(const char *name) {
    Session *s = calloc(1, sizeof(Session));
    if (!s) return NULL;
    if (name)
        strncpy(s->name, name, sizeof(s->name) - 1);
    s->timeout = 30;
    return s;
}

void session_close(Session *s) { free(s); }
const char* session_get_name(const Session *s) { return s->name; }
int session_get_timeout(const Session *s) { return s->timeout; }
