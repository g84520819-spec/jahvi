from pathlib import Path


def delete_file(path: str | Path) -> None:
    Path(path).unlink(missing_ok=True)
