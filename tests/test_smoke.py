"""Smoke checks for branding and importable foundation modules."""

import importlib

import pytest


def test_config_imports():
    assert importlib.import_module("config") is not None


def test_app_name():
    from config import APP_NAME

    assert APP_NAME == "DealerBRAIN"


def test_team_name():
    from config import TEAM_NAME

    assert TEAM_NAME == "Stock Overflow"


def test_app_tagline():
    from config import APP_TAGLINE

    assert APP_TAGLINE == "Agentic After-Sales Stock Rescue Copilot"


@pytest.mark.parametrize(
    "module_name", ["database", "tools", "rules", "workflows", "llm_client", "rag"]
)
def test_foundation_module_imports(module_name):
    assert importlib.import_module(module_name) is not None
