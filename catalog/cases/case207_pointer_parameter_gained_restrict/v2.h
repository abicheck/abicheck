#ifndef CASE207_H
#define CASE207_H

/* Both pointer parameters gained `restrict`. The calling convention is
 * unchanged, but the *caller's* obligation is not: passing overlapping
 * buffers is now undefined behaviour, and the compiler may vectorise the
 * loop on that promise.
 */
void blend(float *restrict dst, const float *restrict src, int n);

#endif
