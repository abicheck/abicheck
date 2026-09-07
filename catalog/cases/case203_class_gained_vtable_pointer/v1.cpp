#include "v1.h"

Handle::Handle() : id_(7) {}
int  Handle::id() const { return id_; }
void Handle::close()    { id_ = -1; }

Handle *make_handle() { return new Handle(); }
