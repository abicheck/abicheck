#ifndef CASE203_H
#define CASE203_H

/* close() became virtual -- the class's *first* virtual member. The compiler
 * inserts a vtable pointer at offset 0, so sizeof(Handle) grows by a pointer
 * width, alignment rises to the pointer's, and every data member shifts.
 */
class Handle {
public:
    Handle();
    int  id() const;
    virtual void close();
private:
    int id_;
};

Handle *make_handle();

#endif
