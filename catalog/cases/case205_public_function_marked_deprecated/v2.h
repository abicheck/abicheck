#ifndef CASE205_H
#define CASE205_H

/* legacy_open() is now marked deprecated. The symbol is still exported and
 * the signature is unchanged, so nothing breaks -- but the library has made
 * a scheduling statement consumers need to see in their release notes.
 */
__attribute__((deprecated)) int legacy_open(const char *name);
int modern_open(const char *name);

#endif
