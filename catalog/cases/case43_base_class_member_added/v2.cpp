#include "v2.hpp"
void Base::describe() { (void)base_id; (void)extra_field; }
void Derived::process() { value = base_id + extra_field + 1; }
