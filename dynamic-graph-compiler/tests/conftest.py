"""
conftest.py — shared pytest fixtures for dynamic-graph-compiler.

All new tests use top-level module imports (from work_packet, from work_ledger,
etc.). This conftest ensures the project root is on sys.path so those imports
resolve correctly regardless of how pytest is invoked.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is on sys.path for top-level module imports
_project_root = Path(__file__).parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

import pytest


@pytest.fixture
def project_root():
    """Project root directory."""
    return Path(__file__).parent.parent


@pytest.fixture
def runs_dir(project_root, tmp_path):
    """A temporary runs directory (sandboxed per-test)."""
    return tmp_path / "runs"


@pytest.fixture
def shapes_dir(project_root, tmp_path):
    """A temporary shapes directory (sandboxed per-test)."""
    return tmp_path / "shapes"


@pytest.fixture
def runtime_factory(tmp_path, shapes_dir):
    """
    Factory that returns a PacketRuntime with isolated temp directories.

    Usage:
        runtime = runtime_factory()
        run_id, packet_id = runtime.create_goal("...", "...")
    """
    from packet_runtime import PacketRuntime

    def _make(profile=None, agent_id="test-agent"):
        return PacketRuntime(
            runs_dir=tmp_path / "runs",
            shapes_dir=shapes_dir,
            profile=profile,
            agent_id=agent_id,
        )

    return _make


@pytest.fixture
def ledger_factory(tmp_path):
    """Factory that returns a WorkLedger with isolated temp directory."""
    from work_ledger import WorkLedger

    def _make():
        return WorkLedger(runs_dir=tmp_path / "runs")

    return _make


@pytest.fixture
def registry_factory(shapes_dir):
    """Factory that returns a ShapeRegistry with isolated temp directory."""
    from shape_registry import ShapeRegistry
    from work_ledger import WorkLedger

    def _make(ledger=None):
        return ShapeRegistry(shapes_dir=shapes_dir, ledger=ledger or WorkLedger(runs_dir=shapes_dir.parent / "runs"))

    return _make