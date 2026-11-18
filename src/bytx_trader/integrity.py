"""Orders are sent only by bytx and bytx-trader as released: their installed .py files must match the wheel's RECORD.

The bytx license (BYTtrade Source-Available License, sections 2(e) and 3) does not permit modified copies of bytx
beyond fixing your own installation, and the order gates of this package live in its code. An edited install is
refused before anything is sent; `uv cache clean` + restart (uvx reuses its cached copy, edits included) or
`pip install --force-reinstall` fixes it. Editable/dev installs carry
no hashes and are not checked.
"""
from __future__ import annotations

import base64
import hashlib
from importlib.metadata import PackageNotFoundError, files
from pathlib import Path
from typing import Any, Iterable, Optional

PACKAGES = ('bytx', 'bytx-trader')
_verdict: dict[str, list[str]] = {}  # per process: a running server already holds the code it loaded


def changed_files(entries: Iterable[Any]) -> list[str]:
    """RECORD entries (importlib.metadata.PackagePath) whose file on disk differs from the recorded sha256."""
    out = []
    for f in entries:
        h = getattr(f, 'hash', None)
        if h is None or h.mode != 'sha256' or not str(f).endswith('.py'):
            continue
        try:
            data = Path(f.locate()).read_bytes()
        except OSError:
            out.append(str(f))
            continue
        if base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode() != h.value:
            out.append(str(f))
    return out


def check(packages: Iterable[str] = PACKAGES) -> None:
    """PermissionError if an installed package was edited; the result is cached for the process."""
    for name in packages:
        if name not in _verdict:
            try:
                _verdict[name] = changed_files(files(name) or [])
            except PackageNotFoundError:
                _verdict[name] = []
        bad = _verdict[name]
        if bad:
            raise PermissionError(
                f'the installed {name} differs from the released package ({", ".join(bad[:3])}'
                f'{" …" if len(bad) > 3 else ""}): nothing is sent. Restore it: run `uv cache clean` and restart the '
                f'assistant (uvx keeps an edited copy otherwise), or `pip install --force-reinstall {name}`. '
                f'Editing bytx is not permitted by its license.')


def reset(name: Optional[str] = None) -> None:
    """For tests: forget the cached verdict."""
    _verdict.clear() if name is None else _verdict.pop(name, None)
