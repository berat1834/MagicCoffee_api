import sys
from pathlib import Path

# Kopru paketi kurulmadan (repo kokunden `pytest` ile) de calissin.
BRIDGE_ROOT = Path(__file__).resolve().parent.parent
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))
