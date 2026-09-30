struct VBase { virtual ~VBase() {} int v; };
struct A : virtual VBase { int a; };
struct B : virtual VBase { int b; };
struct C : A, B { int c; };
