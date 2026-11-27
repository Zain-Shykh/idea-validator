"""The jobs reuse service code (M13's database, M11's engine). Service packages are not
installed: a checkout adds their sources here, the image sets PYTHONPATH.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _service in ("agentops", "verdict"):
    if (_src := str(ROOT / "services" / _service / "src")) not in sys.path:
        sys.path.append(_src)
