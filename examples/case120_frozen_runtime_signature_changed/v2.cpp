#include "v2.h"

namespace mylib {
namespace detail {
namespace r1 {

extern "C" long dispatch(long concurrency) {
    return concurrency * 2;
}

} // namespace r1
} // namespace detail
} // namespace mylib
