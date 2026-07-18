struct Base {
    virtual ~Base() {}
    int a;
};
struct Derived : Base {
    int b;
};
struct Left { virtual ~Left() {} int l; };
struct Right { virtual ~Right() {} int r; };
struct Diamond : Left, Right { int d; };
