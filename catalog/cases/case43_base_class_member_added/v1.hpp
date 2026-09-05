/* case43: Base class member added (libabigail: Added_Base_Class_Data_Member)
 *
 * A data member is added to a base class. This shifts the layout of all
 * derived classes — their fields get pushed to higher offsets.
 *
 * BREAKING: sizeof(Derived) changes, field offsets shift, all callers break.
 *
 * abicheck detects: TYPE_SIZE_CHANGED, TYPE_FIELD_OFFSET_CHANGED
 * libabigail equivalent: Added_Base_Class_Data_Member
 */
#ifndef CASE43_V1_HPP
#define CASE43_V1_HPP

class Base {
public:
    int base_id;
    virtual void describe();
};

class Derived : public Base {
public:
    int value;
    void process();
};

#endif
