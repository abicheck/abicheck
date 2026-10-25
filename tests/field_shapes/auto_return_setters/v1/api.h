#pragma once
template <class T> struct Box { T v; };
struct S {
  int x;
  auto& set_x();
  auto get();
};
auto& ref();
auto val(int);
