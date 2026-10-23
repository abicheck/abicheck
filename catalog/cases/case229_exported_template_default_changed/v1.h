#pragma once
// case229 v1 - exported Box<int> uses default U = T
namespace mylib {
template <typename T, typename U = T>
class Box {
public:
    Box();
    int size() const;
};

extern template class Box<int>;
}  // namespace mylib
