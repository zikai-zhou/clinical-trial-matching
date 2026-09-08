"""Where pair data lives.

Shared by the `verdict` package and the `verdict` command so the two cannot
disagree about which pairs exist.
"""
from __future__ import annotations

import os
import pathlib
from typing import Iterator


def pair_root() -> pathlib.Path:
    """Directory of per-pair stage-1 artifacts.

    $VERDICT_PAIR_DATA overrides; otherwise <repo>/experiments/53_v2_full.
    """
    root = pathlib.Path(os.environ.get(
        "VERDICT_ROOT", pathlib.Path(__file__).resolve().parents[1]))
    return pathlib.Path(os.environ.get("VERDICT_PAIR_DATA",
                                       root / "experiments" / "53_v2_full"))


def iter_pairs() -> Iterator[str]:
    """Yield every available pair id, as '<patient>__<NCT>'."""
    base = pair_root() / "cmsrc_out"
    if not base.exists():
        return
    for pdir in sorted(base.iterdir()):
        if not pdir.is_dir() or pdir.name.startswith("_"):
            continue
        for f in sorted(pdir.glob("*__full.json")):
            yield f'{pdir.name}__{f.name.split("__")[0]}'
