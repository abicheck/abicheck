#include "v2.h"

namespace net {
/* Same declaration and symbol; the library now returns milliseconds. */
long session_timeout() { return 30000; }  // milliseconds
}  // namespace net
