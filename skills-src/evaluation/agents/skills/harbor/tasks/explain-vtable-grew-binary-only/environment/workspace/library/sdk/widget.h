namespace widget {
class Shape {
public:
    virtual ~Shape();
    virtual int area() const = 0;
};
void describe(const Shape &s);
}
