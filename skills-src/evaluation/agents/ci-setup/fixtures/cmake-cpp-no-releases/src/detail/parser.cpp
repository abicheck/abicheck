#include "detail/parser.hpp"
#include <cstdlib>
namespace tinyjson { namespace detail { double parse_number(const std::string &s) { return std::strtod(s.c_str(), nullptr); } } }
