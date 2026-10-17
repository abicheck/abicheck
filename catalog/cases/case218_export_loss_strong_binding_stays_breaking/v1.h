namespace ns {
namespace detail {
template<class T> struct P { P() {} ~P(); int f() { return 1; } };
template<class T> P<T>::~P() {}
}
struct Pub { detail::P<int> p; int g(); detail::P<float>* q(); void* alloc(); };
inline int hdr_inline() { return 5; }
int strong_fn();
}
