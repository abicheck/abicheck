#include "v2.h"

namespace lib {
namespace __detail {

template <typename T>
T walk(T* first, T* last) {
    T acc{};
    for (T* p = first; p != last; ++p) acc += *p;
    return acc;
}

template int walk<int>(int*, int*);
template double walk<double>(double*, double*);

} // namespace __detail
} // namespace lib
