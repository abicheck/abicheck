#ifndef CASE203_H
#define CASE203_H

/* A plain, non-polymorphic class: no virtual member, so no vptr and
 * sizeof(Handle) == sizeof(int). */
class Handle {
public:
    Handle();
    int  id() const;
    void close();
private:
    int id_;
};

Handle *make_handle();

#endif
