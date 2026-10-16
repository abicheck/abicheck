#pragma once
namespace sf {
namespace detail { struct Impl { int v; }; }
template <typename T> struct Box { T value; T get() const { return value; } };
template <typename T> struct Box<T*> { T* ptr; };
template <typename T> T twice(T x) { return x + x; }
template <> inline long twice<long>(long x) { return x * 2; }
struct Base { virtual ~Base(); virtual int f(int) const; virtual void g(); };
struct Derived : Base { int f(int) const override; void g() override; };
typedef void (*handler_t)(int);
void reg(handler_t h);
void on_event(int);
struct Slot { handler_t cb; };
struct Api { Box<detail::Impl> held; Box<int*> ptrs; };
inline int call(const Base& b) { return b.f(1); }
inline int use_twice(int x) { return twice(x) + static_cast<int>(twice(2L)); }
inline void setup() { reg(&on_event); }
inline void fire(Slot& s, int x) { s.cb(x); }
inline void bind(Slot& s) { s.cb = on_event; }
}
