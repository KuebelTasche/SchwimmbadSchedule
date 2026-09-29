import sys
from pathlib import Path

# Projektordner importierbar machen (db, portal, belegung, …)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
