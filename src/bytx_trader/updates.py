"""Tell pip users when a newer bytx-trader or compatible bytx is out. uvx users get it automatically.

One request per package to pypi.org at most once a day, nothing about the user is sent;
BYTX_TRADER_NO_UPDATE_CHECK=1 turns it off. Never installs anything itself.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Callable, Optional

CACHE = Path(os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache') / 'bytx-trader' / 'update.json'
DAY = 86_400


def _ver(s: str) -> Optional[tuple[int, ...]]:
    """'0.2.6' → (0, 2, 6, 0); a pre-release '0.2.6b1' → (0, 2, 6, -1), below its release. Not a version → None.
    shortcut: any suffix (b1, rc1, .dev0, .post1) counts as a pre-release; switch to packaging.version if posts appear."""
    m = re.match(r'(\d+(?:\.\d+)*)(.*)$', s.strip())
    return tuple(int(p) for p in m[1].split('.')) + ((-1,) if m[2] else (0,)) if m else None


def _final(s: str) -> bool:
    return _ver(s) is not None and _ver(s)[-1] == 0


def pypi_releases(name: str) -> list[str]:
    with urllib.request.urlopen(f'https://pypi.org/pypi/{name}/json', timeout=3) as r:
        data = json.load(r)
    return [v for v, files in data['releases'].items() if files and not all(f.get('yanked') for f in files)]


def newest(releases: list[str], series: Optional[tuple[int, int]] = None) -> Optional[str]:
    ok = [(_ver(v), v) for v in releases if _final(v) and (series is None or _ver(v)[:2] == series)]  # skips pre-releases
    return max(ok)[1] if ok else None


def notice(fetch: Callable[[str], list[str]] = pypi_releases, now: Optional[float] = None) -> Optional[str]:
    if os.environ.get('BYTX_TRADER_NO_UPDATE_CHECK'):
        return None
    now = now or time.time()
    try:
        cached = json.loads(CACHE.read_text())
        if now - cached['at'] < DAY:
            return cached['notice']
    except (OSError, ValueError, KeyError):
        pass
    msgs = []
    try:
        mine = version('bytx-trader')
        latest = newest(fetch('bytx-trader'))
        if latest and _ver(latest) > _ver(mine):
            msgs.append(f'bytx-trader {latest} is out (you run {mine})')
        lib = version('bytx')
        # only the series bytx-trader allows (<0.3 today); a new series arrives with a new bytx-trader
        lib_latest = newest(fetch('bytx'), _ver(lib)[:2])
        if lib_latest and _ver(lib_latest) > _ver(lib):
            msgs.append(f'bytx {lib_latest} is out (you run {lib})')
    except (OSError, ValueError, KeyError, TypeError, PackageNotFoundError):
        return None  # offline or PyPI hiccup: never block the server over a hint
    text = ('Update available: ' + '; '.join(msgs) + '. Run: pip install -U bytx-trader bytx '
            '(or start the server with uvx to always get the latest).') if msgs else None
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps({'at': now, 'notice': text}))
    except OSError:
        pass
    return text
