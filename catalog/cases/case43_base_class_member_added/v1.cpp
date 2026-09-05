#include "v1.hpp"
void Base::describe() { (void)base_id; }
void Derived::process() { value = base_id + 1; }
