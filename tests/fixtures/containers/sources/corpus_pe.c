/* Frozen container corpus (tests/fixtures/containers): the PE sample's DLL.
 * corpus_pe.dll beside it is this file built by tests/_container_corpus.py's
 * PE_BUILD command (clang --target=x86_64-windows-gnu, ld.lld, no C runtime),
 * committed so the PE oracles need no PE toolchain. Rebuild it only together
 * with ../pe/ (scripts/freeze_container_corpus.py). */
__declspec(dllexport) int pe_corpus_open(const char *name) { return name ? 1 : 0; }
__declspec(dllexport) void pe_corpus_close(int handle) { (void)handle; }
__declspec(dllexport) int pe_corpus_version = 2;
int DllMainCRTStartup(void *a, unsigned b, void *c) { (void)a; (void)b; (void)c; return 1; }
