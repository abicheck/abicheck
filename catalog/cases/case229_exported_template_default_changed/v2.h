#pragma once
// case229 v2 - default U changed to long
namespace mylib {
template <typename T, typename U = long>
class Box {
public:
    Box();
    int size() const;
};

extern template class Box<int>;
}  // namespace mylib
