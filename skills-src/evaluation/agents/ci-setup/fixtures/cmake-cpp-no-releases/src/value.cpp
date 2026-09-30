#include "tinyjson/value.hpp"
#include "detail/parser.hpp"
namespace tinyjson {
Value::Value() : kind_(0), number_(0) {}
Value::Value(double n) : kind_(1), number_(n) {}
bool Value::is_number() const { return kind_ == 1; }
double Value::as_number() const { return number_; }
std::string Value::dump() const { return is_number() ? std::to_string(number_) : "null"; }
Value parse(const std::string &text) { return Value(detail::parse_number(text)); }
}
