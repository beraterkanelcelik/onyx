"""Unit tests for tracing setup functions."""

from unittest.mock import patch

from onyx.tracing import setup as tracing_setup
from onyx.tracing.dynamic_processor import DynamicTracingProcessor
from onyx.tracing.processors.user_usage_processor import UserUsageTracingProcessor
from onyx.tracing.provider_config import BraintrustConfig
from onyx.tracing.provider_config import EffectiveTracingConfig
from onyx.tracing.provider_config import LangfuseConfig

RESOLVE = "onyx.tracing.dynamic_processor.resolve_effective_tracing_config"
BUILD = "onyx.tracing.dynamic_processor.build_delegates"


def _reset() -> None:
    tracing_setup._initialized = False
    tracing_setup._user_usage_processor = None


def test_setup_tracing_registers_dynamic_and_usage_processors() -> None:
    _reset()
    with (
        patch.object(tracing_setup, "set_trace_processors") as mock_set,
        patch(RESOLVE, return_value=EffectiveTracingConfig()),
        patch(BUILD, return_value=[]),
    ):
        result = tracing_setup.setup_tracing()

        mock_set.assert_called_once()
        (processors,) = mock_set.call_args.args
        assert len(processors) == 2
        assert isinstance(processors[0], DynamicTracingProcessor)
        assert isinstance(processors[1], UserUsageTracingProcessor)
        assert result == ["user_usage"]

    _reset()


def test_setup_tracing_usage_disabled_registers_dynamic_only() -> None:
    _reset()
    with (
        patch.object(tracing_setup, "set_trace_processors") as mock_set,
        patch.object(tracing_setup, "USER_USAGE_TRACKING_ENABLED", False),
        patch(RESOLVE, return_value=EffectiveTracingConfig()),
        patch(BUILD, return_value=[]),
    ):
        result = tracing_setup.setup_tracing()

        (processors,) = mock_set.call_args.args
        assert len(processors) == 1
        assert isinstance(processors[0], DynamicTracingProcessor)
        assert result == []

    _reset()


def test_setup_tracing_is_idempotent() -> None:
    _reset()
    with (
        patch.object(tracing_setup, "set_trace_processors") as mock_set,
        patch(RESOLVE, return_value=EffectiveTracingConfig()),
        patch(BUILD, return_value=[]),
    ):
        tracing_setup.setup_tracing()
        # Second call should be a no-op (already initialized).
        result2 = tracing_setup.setup_tracing()
        assert result2 == []
        mock_set.assert_called_once()

    _reset()


def test_setup_tracing_reports_active_providers() -> None:
    config = EffectiveTracingConfig(
        braintrust=BraintrustConfig(api_key="k", project="p"),
        langfuse=LangfuseConfig(secret_key="s", public_key="pk", host=None),
    )
    _reset()
    with (
        patch.object(tracing_setup, "set_trace_processors"),
        patch(RESOLVE, return_value=config),
        patch(BUILD, return_value=[]),
    ):
        result = tracing_setup.setup_tracing()
        assert result == ["braintrust", "langfuse", "user_usage"]

    _reset()
