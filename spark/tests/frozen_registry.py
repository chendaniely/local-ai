"""The registry the tests of the code's behaviour read: fixtures/frozen-registry.yaml, a frozen copy of
stack/models.yaml, whose models they name throughout, so a change of models doesn't touch them. A test whose point is
the shipped file (test_stack_registry.py, render's and the schema's "real" cases) reads stack/models.yaml itself."""

from pathlib import Path

FROZEN_REGISTRY = Path(__file__).parent / "fixtures" / "frozen-registry.yaml"
