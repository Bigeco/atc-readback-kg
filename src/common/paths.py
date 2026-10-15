from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
DATA_ROOT = PROJECT_ROOT / "data"
RAW_AUDIO_ROOT = DATA_ROOT / "raw" / "liveatc"
STATIC_KB_ROOT = DATA_ROOT / "static_kb"
GOLD_ROOT = DATA_ROOT / "gold"
CONFIG_ROOT = PROJECT_ROOT / "configs"
RESULTS_ROOT = PROJECT_ROOT / "results"

ONTOLOGY_PATH = CONFIG_ROOT / "ontology.json"
AIRLINES_PATH = CONFIG_ROOT / "airlines.json"


def run_dir(run_name: str) -> Path:
    path = RESULTS_ROOT / run_name
    path.mkdir(parents=True, exist_ok=True)
    return path


def display_path(path: Path) -> str:
    resolved = Path(path).expanduser().resolve()
    try:
        return resolved.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return resolved.as_posix()
