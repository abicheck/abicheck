#ifndef CASE186_H
#define CASE186_H

/* The parameter's pointee gained `const`: the pointer itself is still one
 * machine word, passed the same way -- only the mutability contract
 * tightened (the callee now promises not to write through it). Unlike a
 * public struct field, a function parameter is a pure input-direction
 * contract: any caller passing a `char *` (mutable or not) still satisfies
 * `const char *` implicitly, so no existing call site can fail to compile.
 */
void send_buffer(const char *data);

#endif
