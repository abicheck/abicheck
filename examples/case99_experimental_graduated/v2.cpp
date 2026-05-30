#include "v2.h"

namespace lib {

void sort() {}

namespace experimental {

// The experimental alias forwards to the now-stable name. In real
// libraries this is typically an `inline` function or `using` alias;
// keeping a separate definition here is the most portable shape for
// the example harness.
void sort() { ::lib::sort(); }
void other_fn() {}

} // namespace experimental
} // namespace lib
