#ifndef CASE199_H
#define CASE199_H

/* chan_open() gained a second parameter. In C the exported symbol name does
 * not encode arity, so the symbol still resolves and the loader is satisfied
 * -- but every already-compiled caller passes one argument to a callee that
 * now reads two.
 */
int chan_open(const char *name, int flags);
int chan_close(int fd);

#endif
