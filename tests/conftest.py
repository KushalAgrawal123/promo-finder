import os
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
# optional: point at a real (confidential, not committed) show folder to run the full-pack test
PACK = Path(os.environ.get("PROMO_TEST_PACK", "/nonexistent"))


@pytest.fixture
def cfg():
    return yaml.safe_load((ROOT / "config.yaml").read_text())


@pytest.fixture
def synth():
    from promo_finder.parse import parse_show
    return parse_show(ROOT / "examples" / "synthetic_show", title="synthetic")
