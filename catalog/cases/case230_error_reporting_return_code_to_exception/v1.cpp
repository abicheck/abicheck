#include "v1.h"

#include <cstdlib>
#include <stdexcept>

namespace cfg {
int parse_port(const char *text) {
    /* A null pointer is a precondition violation in both versions. */
    if (text == nullptr) throw std::invalid_argument("parse_port: null text");
    char *end = nullptr;
    long value = std::strtol(text, &end, 10);
    if (end == text || *end != '\0' || value < 1 || value > 65535) return -1;
    return static_cast<int>(value);
}
}  // namespace cfg
