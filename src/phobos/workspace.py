import os
import re
import tempfile
from pathlib import Path


def project_path(root: Path, project: str) -> Path:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", project):
        raise ValueError("project must be a lowercase slug of 1–64 characters")
    root = root.resolve()
    path = (root / project).resolve()
    if not path.is_relative_to(root) or path != root / project:
        raise ValueError("project escapes workspace")
    return path


def confined(root: Path, relative: str) -> Path:
    if Path(relative).is_absolute():
        raise ValueError("use a project-relative path")
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("path escapes project")
    if any(part.startswith(".") for part in path.relative_to(root.resolve()).parts):
        raise ValueError("hidden files are not available to agent tools")
    if path.suffix.lower() in {".env", ".pem", ".key"}:
        raise ValueError("credential files are not available to agent tools")
    return path


def atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".phobos-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
