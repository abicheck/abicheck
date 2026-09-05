#ifndef CASE185_H
#define CASE185_H

struct Base {
    virtual ~Base() = default;
    virtual int paint(int x);
};

/* Derived now overrides paint() with the exact same signature as
 * Base::paint(int). This reuses Base's existing vtable slot -- it does not
 * grow Derived's vtable or add a new slot.
 */
struct Derived : Base {
    int paint(int x) override;
    void helper();
};

Base* make_derived();

#endif
