/* case43 v2: Data member added to base class — BREAKING */
#ifndef CASE43_V2_HPP
#define CASE43_V2_HPP

class Base {
public:
    int base_id;
    int extra_field;   /* NEW: added to base — shifts Derived::value */
    virtual void describe();
};

class Derived : public Base {
public:
    int value;
    void process();
};

#endif
