#ifndef CASE216_H
#define CASE216_H

#include <initializer_list>

namespace mylib {
int pick(int);
int pick(std::initializer_list<int>);
/* New overload. pick(int) and pick(double) both take {} by the identity
 * conversion, but pick(std::initializer_list<int>) ranks better for a
 * braced argument ([over.ics.rank]/3.1), so pick({}) still selects it:
 * no call becomes ambiguous. */
int pick(double);
}

#endif
