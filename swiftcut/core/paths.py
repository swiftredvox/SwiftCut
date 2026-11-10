"""Output naming and path handling."""
from __future__ import annotations

import os
import secrets
from pathlib import Path

from .export_plan import CONTAINERS, EXTENSION_TO_CONTAINER


def output_extension(source: str, container: str) -> str:
    """Keep the source's own extension when it maps to the chosen container (e.g. .m4v stays .m4v)."""
    src_ext = Path(source).suffix.lower().lstrip(".")
    if EXTENSION_TO_CONTAINER.get(src_ext) == container:
        return src_ext
    return CONTAINERS[container][1]


def suggested_output_path(source: str, container: str) -> str:
    """Same folder and name as the source, with the extension adjusted to the output format."""
    p = Path(source)
    return str(p.with_name(p.stem + "." + output_extension(source, container)))


def trimmed_alternative(path: str) -> str:
    """'clip.mp4' -> 'clip_trimmed.mp4', adding a counter if that exists too."""
    p = Path(path)
    candidate = p.with_name(f"{p.stem}_trimmed{p.suffix}")
    n = 2
    while candidate.exists():
        candidate = p.with_name(f"{p.stem}_trimmed_{n}{p.suffix}")
        n += 1
    return str(candidate)


def container_for_path(path: str) -> str | None:
    return EXTENSION_TO_CONTAINER.get(Path(path).suffix.lower().lstrip("."))


def ensure_extension(path: str, container: str) -> str:
    """Append the container's extension if the user typed a name without a recognised one."""
    if container_for_path(path):
        return path
    return path + "." + CONTAINERS[container][1]


def same_file(a: str, b: str) -> bool:
    try:
        if os.path.exists(a) and os.path.exists(b):
            return os.path.samefile(a, b)
    except OSError:
        pass
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def temp_path_for(dest: str) -> str:
    """A unique temporary file beside `dest`, so the final rename stays on the same volume."""
    p = Path(dest)
    return str(p.with_name(f"~{p.stem}.swiftcut-{secrets.token_hex(3)}.partial{p.suffix}"))


def check_destination(dest: str) -> str | None:
    """None if `dest` looks writable, else a user-facing reason."""
    folder = Path(dest).parent
    if not folder.exists():
        return f"The folder '{folder}' does not exist."
    if not folder.is_dir():
        return f"'{folder}' is not a folder."
    if Path(dest).is_dir():
        return "A folder with that name already exists."
    probe = folder / f"~swiftcut-write-test-{secrets.token_hex(3)}.tmp"
    try:
        with open(probe, "wb"):
            pass
        probe.unlink()
    except OSError:
        return f"SwiftCut doesn't have permission to write to '{folder}'."
    return None
