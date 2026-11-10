"""Concrete providers are loaded on first attribute access, not with the core."""

import subprocess
import sys
from pathlib import Path


def test_import_reactifact_does_not_load_concrete_providers():
    root = Path(__file__).resolve().parents[1]
    code = """
import sys
import reactifact

assert "reactifact.providers.openrouter" not in sys.modules
assert "reactifact.providers.anthropic" not in sys.modules

from reactifact.providers import openrouter_llm

assert "reactifact.providers.openrouter" in sys.modules
assert callable(openrouter_llm)
"""
    subprocess.run([sys.executable, "-c", code], cwd=root, check=True)
