#ifndef CASE122_GLIBCXX_DUAL_ABI_FLIP_HPP
#define CASE122_GLIBCXX_DUAL_ABI_FLIP_HPP

#include <string>
#include <vector>

/* A public surface that traffics heavily in std::string / std::vector<string>.
 * The mangled names of these symbols embed the libstdc++ dual-ABI tag
 * (std::__cxx11:: vs the legacy std::), so flipping _GLIBCXX_USE_CXX11_ABI
 * rewrites every one of them. */
std::string join(const std::string& a, const std::string& b);
std::string upper(const std::string& s);
std::string repeat(const std::string& s, int n);
std::string trim(const std::string& s);
std::vector<std::string> split(const std::string& s, char sep);
std::string concat(const std::vector<std::string>& parts);

#endif /* CASE122_GLIBCXX_DUAL_ABI_FLIP_HPP */
