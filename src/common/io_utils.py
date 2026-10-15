import json
import os
import random
from pathlib import Path
from typing import Any, Iterable, Iterator, List, Mapping

import numpy as np


def set_seed(seed_value: int = 42):
    random.seed(seed_value)
    np.random.seed(seed_value)
    os.environ["PYTHONHASHSEED"] = str(seed_value)
    try:
        import torch
        torch.manual_seed(seed_value)
        torch.cuda.manual_seed_all(seed_value)
    except ImportError:
        pass


def load_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(obj: Any, path: Path, indent: int = 2):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=indent, ensure_ascii=False)


def iter_jsonl(path: Path) -> Iterator[dict]:
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def load_jsonl(path: Path) -> List[dict]:
    return list(iter_jsonl(path))


def write_jsonl(rows: Iterable[Mapping], path: Path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
