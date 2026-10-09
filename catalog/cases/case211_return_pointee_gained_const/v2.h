#ifndef CASE211_H
#define CASE211_H

/* The returned pointee gained const. The return register and the pointer
 * value are unchanged, so every prebuilt caller keeps working; but source
 * code that stores the result in a plain `char *` no longer compiles
 * (a C++ error, a C constraint violation).
 */
const char *get_name(void);

#endif
