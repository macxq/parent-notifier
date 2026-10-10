import sys
from pathlib import Path

# Dodaj katalog src/ do sys.path, aby testy mogły importować moduły
SRC_PATH = Path(__file__).resolve().parent.parent / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))
