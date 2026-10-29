"""Temporary diagnostic pytest plugin: time every subprocess by exe+caller."""
import atexit, collections, os, subprocess, time, traceback

_tot = collections.Counter()
_n = collections.Counter()
_orig_init = subprocess.Popen.__init__
_orig_wait = subprocess.Popen.wait
_orig_comm = subprocess.Popen.communicate


def _key(args):
    lst = list(args) if isinstance(args, (list, tuple)) else str(args).split()
    exe = os.path.basename(str(lst[0]))
    flags = " ".join(x for x in map(str, lst[1:4]) if x.startswith("-"))
    st = [f"{os.path.basename(f.filename)}:{f.lineno}:{f.name}" for f in traceback.extract_stack()[:-2]
          if "abicheck" in f.filename and "site-packages" not in f.filename and "diag" not in f.filename][-3:]
    return f"{exe} {flags} <- {' <- '.join(reversed(st))}"


def init(self, args, *a, **k):
    self._diag_key = _key(args)
    self._diag_t0 = time.perf_counter()
    self._diag_done = False
    _orig_init(self, args, *a, **k)


def _finish(self):
    if not getattr(self, "_diag_done", True) and self.returncode is not None:
        self._diag_done = True
        _tot[self._diag_key] += time.perf_counter() - self._diag_t0
        _n[self._diag_key] += 1


def wait(self, *a, **k):
    r = _orig_wait(self, *a, **k)
    _finish(self)
    return r


def communicate(self, *a, **k):
    r = _orig_comm(self, *a, **k)
    _finish(self)
    return r


subprocess.Popen.__init__ = init
subprocess.Popen.wait = wait
subprocess.Popen.communicate = communicate


def _report():
    path = os.environ.get("DIAG_OUT", "diag")
    with open(f"{path}-{os.getpid()}.txt", "w", encoding="utf-8") as fh:
        for k, t in _tot.most_common(40):
            fh.write(f"{t:8.1f}s {_n[k]:5d}x  {k}\n")


atexit.register(_report)
