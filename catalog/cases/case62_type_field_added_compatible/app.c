#include <stdio.h>

/* App uses opaque pointer — never allocates or embeds Session directly. */
typedef struct Session Session;

extern Session* session_open(const char *name);
extern void session_close(Session *s);
extern const char* session_get_name(const Session *s);
extern int session_get_timeout(const Session *s);

int main(void) {
    Session *s = session_open("test");
    if (!s) {
        fprintf(stderr, "session_open failed\n");
        return 1;
    }
    printf("name = %s\n", session_get_name(s));
    printf("timeout = %d\n", session_get_timeout(s));
    session_close(s);
    return 0;
}
