// case79 v1 — header advertises descriptor<float> AND descriptor<double>;
// library ships both explicit instantiations.
#pragma once

namespace mylib {

template <typename Float>
class descriptor {
public:
    descriptor();
    Float threshold() const;
    void set_threshold(Float v);
private:
    Float threshold_;
};

// Public surface: both float and double are advertised as supported.
extern template class descriptor<float>;
extern template class descriptor<double>;

}  // namespace mylib
