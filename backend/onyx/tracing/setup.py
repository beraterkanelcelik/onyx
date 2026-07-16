"""Registers the DynamicTracingProcessor, which resolves the live (DB-or-env)
provider config at runtime so config changes apply without a restart. The
per-user usage recorder rides alongside it — independent of external tracing
backends, it must not be affected by provider connect/disconnect."""

from onyx.configs.app_configs import USER_USAGE_TRACKING_ENABLED
from onyx.tracing.dynamic_processor import DynamicTracingProcessor
from onyx.tracing.framework import set_trace_processors
from onyx.utils.logger import setup_logger

logger = setup_logger()

_initialized = False
_dynamic_processor: DynamicTracingProcessor | None = None
_user_usage_processor: object | None = None


def setup_tracing() -> list[str]:
    """Register the dynamic tracing processor (plus the usage recorder) and do
    an initial config read. Idempotent; returns the provider names active at
    startup."""
    global _initialized, _dynamic_processor, _user_usage_processor
    if _initialized:
        logger.debug("Tracing already initialized, skipping")
        return []

    _dynamic_processor = DynamicTracingProcessor()
    processors: list = [_dynamic_processor]

    if USER_USAGE_TRACKING_ENABLED:
        try:
            from onyx.tracing.processors.user_usage_processor import (
                UserUsageTracingProcessor,
            )

            _user_usage_processor = UserUsageTracingProcessor()
            processors.append(_user_usage_processor)
        except Exception as e:
            logger.error("Failed to initialize user usage tracking: %s", e)
    else:
        logger.info("User usage tracking disabled, skipping")

    set_trace_processors(processors)
    config = _dynamic_processor.reconcile(force=True)
    _initialized = True

    initialized_providers = config.active_provider_names() if config else []
    if _user_usage_processor is not None:
        initialized_providers.append("user_usage")
    if initialized_providers:
        logger.notice(
            "Tracing initialized with providers: %s", ", ".join(initialized_providers)
        )
    else:
        logger.info("No tracing providers configured")

    return initialized_providers


def shutdown_tracing() -> None:
    """Flush buffered usage to the DB on shutdown. Call before disposing the DB
    engines (the drain thread writes through them) so queued records aren't lost."""
    from onyx.tracing.processors.user_usage_processor import UserUsageTracingProcessor

    if isinstance(_user_usage_processor, UserUsageTracingProcessor):
        try:
            _user_usage_processor.shutdown()
        except Exception:
            logger.exception("Failed to flush user usage on shutdown")
