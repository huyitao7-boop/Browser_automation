from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def resolve_from_project(path: Path) -> Path:
    return path if path.is_absolute() else project_root() / path
