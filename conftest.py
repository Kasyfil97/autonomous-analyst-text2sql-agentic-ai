"""Ensure the repo root is importable so tests can `import bedrock_session` etc."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
