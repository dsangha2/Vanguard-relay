"""Import this first, before `import relay`: tests must never touch the real data folder.

RELAY_DATA is always replaced with a fresh temp folder, whatever value the process inherited.
"""
import os, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['RELAY_DATA'] = tempfile.mkdtemp(prefix='relay-test-data-')
