#include "v2.h"

Handle::Handle() : id_(7) {}
int  Handle::id() const   { return id_; }
void Handle::close()      { id_ = -1; }
bool Handle::is_open() const { return id_ >= 0; }

Handle *make_handle() { return new Handle(); }
