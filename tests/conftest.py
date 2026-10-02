"""Pytest configuration and shared fixtures for budget-bot test suite."""

import os
import sys
import pytest

# Ensure project root is available in sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

@pytest.fixture(autouse=True)
def isolate_environment():
    """Snapshot and restore os.environ around each test to prevent cross-test pollution."""
    old_env = os.environ.copy()
    yield
    os.environ.clear()
    os.environ.update(old_env)
