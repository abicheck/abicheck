/* good.h — v2: Session gains a new field, but callers still use opaque pointer.
   New accessor function added for the new field. */
#ifndef MYLIB_H
#define MYLIB_H

typedef struct Session Session;

Session* session_open(const char *name);
void session_close(Session *s);
const char* session_get_name(const Session *s);
int session_get_timeout(const Session *s);
int session_get_priority(const Session *s);  /* NEW in v2 */

#endif
