#ifndef CASE185_H
#define CASE185_H

struct Base {
    virtual ~Base() = default;
    virtual int paint(int x);
};

/* Derived does not override paint() -- it inherits Base::paint() as-is and
 * only adds a plain, non-virtual method.
 */
struct Derived : Base {
    void helper();
};

/* Forces the compiler to materialize Derived's vtable in the library itself
 * (Derived has no "key function" of its own in v1, so without a use site
 * its vtable would be a weak symbol emitted only where ODR-used).
 */
Base* make_derived();

#endif
