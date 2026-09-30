from pathlib import Path
import threading

_ACTIVE_TEMP_PATHS = set()
_ACTIVE_TEMP_PATHS_LOCK = threading.Lock()


def register_temp_path(path: str | Path) -> None:
    with _ACTIVE_TEMP_PATHS_LOCK:
        _ACTIVE_TEMP_PATHS.add(str(Path(path).resolve()))


def unregister_temp_path(path: str | Path) -> None:
    with _ACTIVE_TEMP_PATHS_LOCK:
        _ACTIVE_TEMP_PATHS.discard(str(Path(path).resolve()))


def is_temp_path_active(path: str | Path) -> bool:
    with _ACTIVE_TEMP_PATHS_LOCK:
        return str(Path(path).resolve()) in _ACTIVE_TEMP_PATHS


def delete_file(path: str | Path) -> None:
    Path(path).unlink(missing_ok=True)
