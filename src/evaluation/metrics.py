import math
from typing import Dict, Iterable, List, Sequence, Tuple


def wilson_ci(successes: int, n: int, z: float = 1.96) -> Tuple[float, float]:
    """Wilson score interval (Wilson 1927); stable for the small samples used in E2/E3."""
    if n == 0:
        return (0.0, 0.0)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4))


def rate(successes: int, n: int) -> Dict[str, float]:
    lo, hi = wilson_ci(successes, n)
    return {"k": successes, "n": n, "rate": round(successes / n, 4) if n else None, "ci95": [lo, hi]}


def word_error_rate(reference: str, hypothesis: str) -> float:
    ref, hyp = reference.lower().split(), hypothesis.lower().split()
    if not ref:
        return 0.0 if not hyp else 1.0
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h))
        prev = cur
    return prev[-1] / len(ref)


def set_f1(predicted: Iterable, gold: Iterable) -> Dict[str, float]:
    pred, ref = list(predicted), list(gold)
    remaining = list(ref)
    tp = 0
    for item in pred:
        if item in remaining:
            remaining.remove(item)
            tp += 1
    precision = tp / len(pred) if pred else (1.0 if not ref else 0.0)
    recall = tp / len(ref) if ref else (1.0 if not pred else 0.0)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"tp": tp, "pred": len(pred), "gold": len(ref), "precision": round(precision, 4),
            "recall": round(recall, 4), "f1": round(f1, 4)}
