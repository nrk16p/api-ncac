import sys
from pathlib import Path

# Pipeline scripts import their siblings by module name (they run as files), so tests do the same.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
