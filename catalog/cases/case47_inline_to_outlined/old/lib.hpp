/* case47: Inline function moved to outlined (and mangling / symbol appears)
 *
 * In v1 the method is defined inline in the header — no exported symbol.
 * In v2 it is moved out-of-line — the symbol is now exported. Any consumer
 * compiled against v1 headers that called the inline gets a different code
 * path; callers linking only the .so may get the symbol they expect.
 *
 * This is a BAD PRACTICE / source-level change that abicheck surfaces via
 * FUNC_ADDED (new exported symbol) combined with detecting the inline removal.
 *
 * abicheck detects: FUNC_ADDED (compatible addition of exported symbol)
 * libabigail equivalent: Function_Symbol_Added / inline→outlined
 */
#ifndef CASE47_V1_HPP
#define CASE47_V1_HPP

class Calculator {
public:
    /* Inline in v1 — no symbol exported */
    inline int add(int a, int b) { return a + b; }
    int multiply(int a, int b);
    int subtract(int a, int b);
};

#endif
