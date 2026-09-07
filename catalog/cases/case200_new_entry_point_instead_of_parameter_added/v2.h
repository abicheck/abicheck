#ifndef CASE200_H
#define CASE200_H

/* The new `flags` capability arrives as a *second entry point*. chan_open()
 * keeps its original arity, so every already-compiled caller keeps working
 * and every existing call site still compiles.
 */
int chan_open(const char *name);
int chan_open_ex(const char *name, int flags);
int chan_close(int fd);

#endif
