import importlib.util
from pathlib import Path
from types import ModuleType

import numpy as np
import pytest
from PIL import Image

SCRIPT = Path(__file__).parent.parent / "tools" / "build_marks.py"


def _tool() -> ModuleType:
    spec = importlib.util.spec_from_file_location("build_marks", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TOOL = _tool()
FIX = "run uv run python tools/build_marks.py"


def test_every_mark_has_a_source() -> None:
    built = {path.stem for path in TOOL.OUT.glob("*.png")}
    assert built == {source.stem for source in TOOL.sources()}, FIX


@pytest.mark.parametrize("source", TOOL.sources(), ids=lambda source: source.stem)
def test_mark_matches_its_source(source: Path) -> None:
    with Image.open(TOOL.OUT / f"{source.stem}.png") as saved:
        committed = np.asarray(saved.convert("RGBA"))
    assert np.array_equal(np.asarray(TOOL.build(source)), committed), FIX
