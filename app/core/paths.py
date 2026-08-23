"""Filesystem isolation and directory boundary enforcement for MiniSashimi.

Provides secure path resolution and containment verification (blocking path traversal,
symlink escapes, and null-byte injection) along with file/directory size quotas.
"""

from __future__ import annotations
import os
from pathlib import Path


class UnsafePathError(ValueError):
    """Raised when a user-controlled path attempts to escape its configured root directory."""
    pass


def configured_root(env_name: str, default: str) -> Path:
    """Resolves a configured directory root from environment variables, creating it with 0700 permissions."""
    root = Path(os.getenv(env_name, default)).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(root, 0o700)
    except OSError:
        pass
    return root


def resolve_under_root(raw_path: str, root: Path, *, must_exist: bool = False) -> Path:
    """Resolves a target path and verifies it strictly resides within the specified root directory.
    
    Args:
        raw_path: Input path string from request or configuration.
        root: Permitted root Path object.
        must_exist: If True, raises FileNotFoundError if target does not exist.
        
    Returns:
        Resolved safe Path object.
        
    Raises:
        UnsafePathError: If path contains null bytes or escapes root directory.
    """
    if not isinstance(raw_path, str) or not raw_path.strip() or "\x00" in raw_path:
        raise UnsafePathError("invalid path")

    root_resolved = root.expanduser().resolve()
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = root_resolved / candidate
    candidate = candidate.resolve(strict=False)

    try:
        if os.path.commonpath((str(root_resolved), str(candidate))) != str(root_resolved):
            raise UnsafePathError("path is outside the permitted root")
    except ValueError as exc:
        raise UnsafePathError("path is on a different filesystem") from exc

    if must_exist and not candidate.exists():
        raise FileNotFoundError(str(candidate))
    return candidate


def ensure_regular_file(path: Path, *, max_bytes: int) -> None:
    """Verifies that the target is a regular file and does not exceed the byte size limit."""
    if not path.is_file():
        raise UnsafePathError("path is not a regular file")
    if path.stat().st_size > max_bytes:
        raise UnsafePathError("file exceeds the configured size limit")


def ensure_directory_size(root: Path, *, max_bytes: int) -> int:
    """Calculates total directory size and ensures it stays within max_bytes limit."""
    total = 0
    for item in root.rglob("*"):
        if item.is_file():
            total += item.stat().st_size
            if total > max_bytes:
                raise UnsafePathError("output exceeds the configured size limit")
    return total
