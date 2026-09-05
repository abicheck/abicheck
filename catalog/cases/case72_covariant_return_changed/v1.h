#ifndef SHAPE_H
#define SHAPE_H

class Shape {
public:
    virtual ~Shape();
    /* v1: clone() returns Shape* — standard covariant return */
    virtual Shape *clone() const;
    virtual int area() const = 0;
};

class Circle : public Shape {
    int radius_;
public:
    Circle(int r);
    /* v1: covariant return — returns Circle* (subtype of Shape*) */
    Circle *clone() const override;
    int area() const override;
    int radius() const;
};

#endif
