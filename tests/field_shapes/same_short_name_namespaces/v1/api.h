#pragma once
namespace algo_a { struct Parameter { int a; }; int fa(Parameter*); }
namespace algo_b { struct Parameter { int b; }; int fb(Parameter*); }
namespace algo_c { struct Parameter { int c; }; int fc(Parameter*); }
namespace algo_d { namespace inner { struct Parameter { int d; }; }
                   int fd(inner::Parameter*); }
struct Holder { struct Parameter { int h; }; Parameter p; };
int fh(Holder*);
