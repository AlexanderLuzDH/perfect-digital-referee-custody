#!/usr/bin/env python3
"""Atomic exclusive regular-file creation for fixed ceremony outputs."""

from __future__ import annotations

import os
import stat
from pathlib import Path


def write_new_regular(
    path: Path,
    raw: bytes,
    expected_name: str,
    limit: int,
) -> None:
    if (
        type(raw) is not bytes
        or len(raw) > limit
        or path.parent != Path(".")
        or path.name != expected_name
    ):
        raise ValueError("invalid output request")
    root = Path.cwd().resolve(strict=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path.name, flags, 0o600)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise ValueError("output is not regular")
        offset = 0
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written <= 0:
                raise OSError("short output write")
            offset += written
        os.fsync(descriptor)
        linked = os.stat(path.name, follow_symlinks=False)
        if (
            not stat.S_ISREG(linked.st_mode)
            or linked.st_dev != opened.st_dev
            or linked.st_ino != opened.st_ino
            or linked.st_size != len(raw)
            or path.resolve(strict=True).parent != root
        ):
            raise ValueError("output identity changed")
    finally:
        os.close(descriptor)
