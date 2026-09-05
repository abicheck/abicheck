#ifndef SHAPE_H
#define SHAPE_H

class Shape {
public:
    virtual ~Shape();
    virtual Shape *clone() const;
    virtual int area() const = 0;
};

/* v2: new intermediate class inserted into hierarchy */
class Drawable : public Shape {
    int color_;
public:
    Drawable();
    virtual int color() const;
};

class Circle : public Drawable {
    int radius_;
public:
    Circle(int r);
    /* v2: covariant return type changes from Circle* to Drawable*
       because the class hierarchy changed.  The vtable thunk that
       adjusts the this-pointer for the covariant return must be
       regenerated — old vtables have the wrong thunk. */
    Drawable *clone() const override;
    int area() const override;
    int radius() const;
};

#endif
