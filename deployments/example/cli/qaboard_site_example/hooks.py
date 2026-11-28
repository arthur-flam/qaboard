"""Hooks of the example site (entry points in the qaboard.hooks group, see pyproject.toml)."""
import os
from pathlib import Path


def fix_permissions(path: Path) -> None:
    """Called for folders `qa` created, e.g. from Windows, where they don't get the permissions others need"""
    if os.name != 'nt':
        os.chmod(path, 0o775)
