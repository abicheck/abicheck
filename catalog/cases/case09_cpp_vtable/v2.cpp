/* recolor() inserted before resize() — vtable offset of resize() shifts */
class Widget {
public:
    virtual int draw();
    virtual int recolor();
    virtual int resize();
};
int Widget::draw()    { return 10; }
int Widget::recolor() { return 99; }
int Widget::resize()  { return 20; }

/* Caller takes ownership; must delete the returned Widget. */
extern "C" Widget* make_widget() { return new Widget(); }
