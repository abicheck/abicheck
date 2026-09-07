#ifndef CASE207_H
#define CASE207_H

/* No aliasing restriction: a caller may legally pass overlapping buffers. */
void blend(float *dst, const float *src, int n);

#endif
