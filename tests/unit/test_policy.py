"""Tests for the safety policy."""

from __future__ import annotations

import pytest

from print_engineer.core.policy import PermissivePolicy, PolicyContext, PrinterAction, SafetyPolicy


@pytest.fixture
def policy() -> PermissivePolicy:
    return PermissivePolicy()


def test_policy_is_safety_policy(policy: PermissivePolicy) -> None:
    assert isinstance(policy, SafetyPolicy)


@pytest.mark.parametrize(
    "action",
    [PrinterAction.START_PRINT, PrinterAction.STOP_PRINT, PrinterAction.SET_TEMPERATURE],
)
def test_dangerous_actions_require_confirmation(
    policy: PermissivePolicy, action: PrinterAction
) -> None:
    decision = policy.evaluate(PolicyContext(action=action))
    assert not decision.allowed
    assert decision.requires_confirmation
    assert decision.reason


def test_confirmed_dangerous_action_allowed(policy: PermissivePolicy) -> None:
    decision = policy.evaluate(PolicyContext(action=PrinterAction.START_PRINT, confirm=True))
    assert decision.allowed
    assert not decision.requires_confirmation


@pytest.mark.parametrize(
    "action",
    [PrinterAction.PAUSE_PRINT, PrinterAction.RESUME_PRINT],
)
def test_safe_actions_allowed_without_confirmation(
    policy: PermissivePolicy, action: PrinterAction
) -> None:
    decision = policy.evaluate(PolicyContext(action=action))
    assert decision.allowed
