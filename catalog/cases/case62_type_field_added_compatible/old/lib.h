/* bad.h — v1: opaque handle — callers never see the struct layout.
   All allocation and access goes through library functions. */
#ifndef MYLIB_H
#define MYLIB_H

typedef struct Session Session;

Session* session_open(const char *name);
void session_close(Session *s);
const char* session_get_name(const Session *s);
int session_get_timeout(const Session *s);

#endif
