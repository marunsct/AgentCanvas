"""Compatibility shim: the validator lives in `agentcanvas.validation` (rule framework, M1-T8)."""

from agentcanvas.validation import Diagnostic, has_errors, validate

__all__ = ["Diagnostic", "has_errors", "validate"]
