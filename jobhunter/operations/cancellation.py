"""Cooperative cancellation at safe boundaries; never kill threads or interrupt SQLite writes."""

import logging
import threading

logger = logging.getLogger(__name__)
_state = threading.local()


class Cancelled(BaseException):
    """Leave ordinary provider-error handlers without treating a user stop as a failure."""


def bind(callback=None):
    """Install or clear the current worker's cancellation predicate."""
    _state.callback = callback


def check():
    """Stop at a record or request boundary if the owning pipeline was cancelled."""
    callback = getattr(_state, 'callback', None)
    if callback and callback():
        logger.info('Pipeline cancellation acknowledged')
        raise Cancelled()
