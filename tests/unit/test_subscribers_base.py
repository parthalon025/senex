"""Tests for the Subscriber protocol + named exceptions (M9 Task 9.1)."""
from __future__ import annotations

import inspect
import typing

import pytest

from senex.subscribers.base import (
    Subscriber,
    SubscriberQueueFull,
    SubscriberShutdownError,
)


def test_subscriber_protocol_has_name_attr() -> None:
    hints = typing.get_type_hints(Subscriber)
    assert hints["name"] is str


def test_subscriber_protocol_has_queue_capacity_attr() -> None:
    hints = typing.get_type_hints(Subscriber)
    assert hints["queue_capacity"] is int


def test_subscriber_protocol_has_drop_policy_attr() -> None:
    hints = typing.get_type_hints(Subscriber)
    drop_policy_t = hints["drop_policy"]
    # Literal["block", "drop_oldest", "drop_token_only"]
    args = typing.get_args(drop_policy_t)
    assert set(args) == {"block", "drop_oldest", "drop_token_only"}


def test_subscriber_protocol_has_consume_method() -> None:
    members = dict(inspect.getmembers(Subscriber))
    assert "consume" in members
    sig = inspect.signature(members["consume"])
    assert "event" in sig.parameters


def test_subscriber_protocol_has_shutdown_method() -> None:
    members = dict(inspect.getmembers(Subscriber))
    assert "shutdown" in members


def test_subscriber_queue_full_is_exception_subclass() -> None:
    assert issubclass(SubscriberQueueFull, Exception)


def test_subscriber_shutdown_error_is_exception_subclass() -> None:
    assert issubclass(SubscriberShutdownError, Exception)


def test_subscriber_queue_full_can_be_raised() -> None:
    with pytest.raises(SubscriberQueueFull):
        raise SubscriberQueueFull("queue saturated")


def test_subscriber_shutdown_error_can_be_raised() -> None:
    with pytest.raises(SubscriberShutdownError):
        raise SubscriberShutdownError("flush timeout")
