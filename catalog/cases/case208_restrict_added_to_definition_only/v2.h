#ifndef CASE208_H
#define CASE208_H

/* Unchanged: the published contract still makes no aliasing promise. The
 * implementation gained `restrict` internally (see v2.c), which is a
 * statement about how *that translation unit* is compiled, not about what
 * callers must guarantee.
 */
void blend(float *dst, const float *src, int n);

#endif
