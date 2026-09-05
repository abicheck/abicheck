#ifndef CASE184_INTERNAL_H
#define CASE184_INTERNAL_H

/* MODE_B's numeric value changed. This is a real layout/value change to
 * InternalMode -- but InternalMode is confined to a private header and
 * unreachable from any public function signature, so it is not part of the
 * public ABI surface (ADR-024).
 */
typedef enum {
    MODE_A = 0,
    MODE_B = 9
} InternalMode;

#endif
