#ifndef CASE206_H
#define CASE206_H

/* Deprecated: prefer modern_open(); scheduled for removal in 3.0.
 *
 * The intent is stated only in this comment -- no `__attribute__((deprecated))`
 * and no `[[deprecated]]`. The compiler never sees it, so neither does any
 * declaration-based tool.
 */
int legacy_open(const char *name);
int modern_open(const char *name);

#endif
