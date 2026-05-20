// case79 v2 — header STILL advertises descriptor<float> AND descriptor<double>;
// library now ships ONLY descriptor<float>. Header lies about binary surface.
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

// Header unchanged — both instantiations still advertised.
extern template class descriptor<float>;
extern template class descriptor<double>;

}  // namespace mylib
