#include "v2.h"

#include <cstdlib>
#include <stdexcept>

namespace cfg {
/* Same declaration, same mangled symbol, no noexcept either side, same
 * imports -- but invalid input now throws instead of returning -1. */
int parse_port(const char *text) {
    if (text == nullptr) throw std::invalid_argument("parse_port: null text");
    char *end = nullptr;
    long value = std::strtol(text, &end, 10);
    if (end == text || *end != '\0' || value < 1 || value > 65535)
        throw std::invalid_argument("parse_port: not a port number");
    return static_cast<int>(value);
}
}  // namespace cfg
