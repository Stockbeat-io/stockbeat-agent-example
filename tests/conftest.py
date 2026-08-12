import os
import sys

# Ensure the project root is importable and DRY_RUN stays ON during tests.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DRY_RUN", "true")
