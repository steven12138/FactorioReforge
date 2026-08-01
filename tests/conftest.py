import sys
from pathlib import Path

# Let tests import the package without an editable install.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
