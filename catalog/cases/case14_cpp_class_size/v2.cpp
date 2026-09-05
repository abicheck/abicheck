#include "v2.h"
#include <cstring>
Buffer::Buffer() { std::memset(data, 0, sizeof(data)); }
int Buffer::size() { return 128; }
extern "C" Buffer* make_buffer() { return new Buffer(); }
