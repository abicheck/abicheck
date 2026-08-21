
class Widget {
public:
    virtual int draw();
    virtual int recolor();
    virtual int resize();
};
int Widget::draw()    { return 10; }
int Widget::recolor() { return 99; }
int Widget::resize()  { return 20; }


extern "C" Widget* make_widget() { return new Widget(); }
