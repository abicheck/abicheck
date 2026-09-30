#include "kv/kv.h"
namespace kv {
bool Store::put(const std::string &, const std::string &) { return true; }
bool Store::get(const std::string &, std::string *) const { return false; }
}
