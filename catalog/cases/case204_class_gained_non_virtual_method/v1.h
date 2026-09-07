#ifndef CASE204_H
#define CASE204_H

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
