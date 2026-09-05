#ifndef CASE184_INTERNAL_H
#define CASE184_INTERNAL_H

/* Private implementation detail -- not part of the installed public header
 * set. It happens to be transitively #include-d by v1.h (a common pattern:
 * an umbrella public header pulling in a private detail header), but only
 * v1.h itself is ever passed to abicheck via -H/--header. abicheck's header
 * AST parser classifies each declaration by the file it actually came from,
 * so InternalMode is tagged PRIVATE_HEADER, not PUBLIC_HEADER, even though
 * it's reachable from the public compilation unit.
 */
typedef enum {
    MODE_A = 0,
    MODE_B = 1
} InternalMode;

#endif
