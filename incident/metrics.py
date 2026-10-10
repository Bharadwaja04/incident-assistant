import resource, sys, time, statistics

class Timer:
    def __enter__(self): self.t = time.perf_counter(); return self
    def __exit__(self, *a): self.s = time.perf_counter() - self.t

def peak_rss_mb() -> float:
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return r / 1e6 if sys.platform == "darwin" else r / 1e3

def pct(xs, p):
    if not xs: return 0.0
    xs = sorted(xs); k = min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))
    return xs[k]

def norm_words(t: str) -> list:
    """Compare words, not punctuation or style: 'a.m.' == 'AM', 'Room 2.' == 'room 2'."""
    import re
    t = t.lower().replace("a.m.", "am").replace("p.m.", "pm")
    t = re.sub(r"(\d)\s+(am|pm)\b", r"\1\2", t)          # '10 am' -> '10am'
    t = re.sub(r"[^a-z0-9: ]", " ", t)
    t = re.sub(r"(?<![0-9]):|:(?![0-9])", " ", t)           # drop colons that are not inside a time
    return t.split()


def wer(ref: str, hyp: str) -> float:
    r, h = norm_words(ref), norm_words(hyp)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = d[j]
            d[j] = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev = cur
    return d[len(h)] / max(1, len(r))
