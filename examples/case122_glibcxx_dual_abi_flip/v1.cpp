#include "v1.hpp"

std::string join(const std::string& a, const std::string& b) { return a + b; }
std::string upper(const std::string& s) { return s; }
std::string repeat(const std::string& s, int n) {
    std::string r;
    for (int i = 0; i < n; ++i) r += s;
    return r;
}
std::string trim(const std::string& s) { return s; }
std::vector<std::string> split(const std::string& s, char) { return {s}; }
std::string concat(const std::vector<std::string>& parts) {
    std::string r;
    for (const auto& p : parts) r += p;
    return r;
}
