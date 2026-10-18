/* Compiler-identification probes: hand-written C for small functions of the
 * pinned EBOOT (ULUS10505 v1.00). tools/probe_match.py compiles this file and
 * compares each func_XXXXXXXX with the game at that address. Our own code,
 * written from the disassembly; no game bytes are in this file. */
extern char D_08B5BCA4[];
extern int D_08BD1800;
void func_08807F44(int, void *, int);
void func_08A8BB44(void);
void func_08804130(void);
struct N { struct N *next; struct N *prev; };

void func_088040B0(void) { func_08807F44(0, D_08B5BCA4, 0); }
void func_08804210(int a) { D_08BD1800 = a; if (a == 0) func_08A8BB44(); }
void func_08804238(void) { func_08804130(); }
struct N *func_08804364(struct N *n) {
    struct N *r = n->prev;
    if (n->next) n->next->prev = n->prev;
    if (n->prev) n->prev->next = n->next;
    n->prev = 0;
    n->next = 0;
    return r;
}
