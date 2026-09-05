#include "v1.h"

namespace mylib {
namespace detail {
namespace r1 {

extern "C" int dispatch(int concurrency) {
    return concurrency * 2;
}

} // namespace r1
} // namespace detail
} // namespace mylib
