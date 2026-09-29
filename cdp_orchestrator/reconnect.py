"""Reconnect manager — failover state machine with exponential backoff."""

from __future__ import annotations

import asyncio
import logging
import random
from enum import Enum, auto
from typing import Awaitable, Callable, Optional

from .browser_spawner import ensure_browser_running, BrowserLaunchError
from .healthcheck import HealthMonitor, ConnectionState

logger = logging.getLogger(__name__)


class BrowserUnrecoverableError(Exception):
    """Raised when browser cannot be recovered after max retry attempts."""


class ReconnectState(Enum):
    CONNECTED = auto()
    RECONNECTING = auto()
    DISCONNECTED = auto()
    UNRECOVERABLE = auto()


class ReconnectManager:
    """Manages CDP connection lifecycle with automatic failover.

    State machine:
        CONNECTED -> (disconnect detected) -> RECONNECTING
        RECONNECTING -> (success) -> CONNECTED
        RECONNECTING -> (max retries) -> UNRECOVERABLE
    """

    def __init__(
        self,
        max_attempts: int = 5,
        initial_delay: float = 1.0,
        max_delay: float = 10.0,
        on_reconnected: Optional[Callable[[dict], Awaitable[None]]] = None,
        on_unrecoverable: Optional[Callable[[], Awaitable[None]]] = None,
    ):
        self._max_attempts = max_attempts
        self._initial_delay = initial_delay
        self._max_delay = max_delay
        self._on_reconnected = on_reconnected
        self._on_unrecoverable = on_unrecoverable

        self._state = ReconnectState.DISCONNECTED
        self._monitor: Optional[HealthMonitor] = None
        self._session_info: Optional[dict] = None
        self._stop_event: Optional[asyncio.Event] = None

    @property
    def state(self) -> ReconnectState:
        return self._state

    @property
    def session_info(self) -> Optional[dict]:
        return self._session_info

    async def start(self) -> dict:
        """Start the reconnect manager — ensures browser is running and monitors."""
        self._stop_event = asyncio.Event()

        # Initial connection
        self._session_info = await ensure_browser_running()
        self._state = ReconnectState.CONNECTED

        # Start health monitor with disconnect callback
        self._monitor = HealthMonitor(
            on_disconnected=self._handle_disconnect,
            on_healthy=self._handle_healthy,
        )
        await self._monitor.start()

        logger.info("ReconnectManager started, state=CONNECTED")
        return self._session_info

    async def stop(self) -> None:
        """Gracefully stop monitoring and cleanup."""
        if self._stop_event:
            self._stop_event.set()
        if self._monitor:
            await self._monitor.stop()
        self._state = ReconnectState.DISCONNECTED
        logger.info("ReconnectManager stopped")

    async def _handle_disconnect(self, reason: str) -> None:
        """Handle disconnect event from HealthMonitor."""
        if self._state == ReconnectState.RECONNECTING:
            logger.debug("Already reconnecting, ignoring duplicate disconnect")
            return

        logger.warning("Disconnect detected: %s", reason)
        self._state = ReconnectState.RECONNECTING

        # Invalidate stale session
        self._session_info = None

        # Attempt reconnection with exponential backoff
        await self._reconnect_with_backoff()

    async def _handle_healthy(self) -> None:
        """Handle healthy event from HealthMonitor."""
        if self._state == ReconnectState.RECONNECTING:
            logger.info("Connection restored, state=CONNECTED")
            self._state = ReconnectState.CONNECTED

    async def _reconnect_with_backoff(self) -> None:
        """Reconnect with exponential backoff + jitter."""
        for attempt in range(1, self._max_attempts + 1):
            if self._stop_event and self._stop_event.is_set():
                logger.info("Reconnect aborted (stop requested)")
                return

            delay = self._compute_delay(attempt)
            logger.info(
                "Reconnect attempt %d/%d (delay: %.1fs)",
                attempt,
                self._max_attempts,
                delay,
            )

            await asyncio.sleep(delay)

            try:
                # Revive browser if needed
                self._session_info = await ensure_browser_running()

                # Success
                logger.info("Reconnected successfully on attempt %d", attempt)
                self._state = ReconnectState.CONNECTED

                if self._on_reconnected:
                    try:
                        await self._on_reconnected(self._session_info)
                    except Exception as e:
                        logger.error("Error in on_reconnected callback: %s", e)

                return

            except (BrowserLaunchError, OSError) as e:
                logger.warning("Reconnect attempt %d failed: %s", attempt, e)

        # Max attempts reached
        logger.error("Max reconnect attempts (%d) reached", self._max_attempts)
        self._state = ReconnectState.UNRECOVERABLE

        if self._on_unrecoverable:
            try:
                await self._on_unrecoverable()
            except Exception as e:
                logger.error("Error in on_unrecoverable callback: %s", e)

        raise BrowserUnrecoverableError(
            f"Browser unrecoverable after {self._max_attempts} attempts"
        )

    def _compute_delay(self, attempt: int) -> float:
        """Compute delay with exponential backoff + full jitter."""
        # Exponential: initial * 2^(attempt-1)
        exp_delay = self._initial_delay * (2 ** (attempt - 1))
        # Cap at max_delay
        capped = min(exp_delay, self._max_delay)
        # Full jitter: random value between 0 and capped
        jittered = random.uniform(0, capped)
        return jittered


async def main():
    """CLI entrypoint demonstrating full lifecycle."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    async def on_reconnected(session: dict) -> None:
        print(f"\n[EVENT] Reconnected! Browser: {session.get('Browser', 'unknown')}")

    async def on_unrecoverable() -> None:
        print("\n[EVENT] Browser unrecoverable!")

    manager = ReconnectManager(
        max_attempts=5,
        initial_delay=1.0,
        max_delay=10.0,
        on_reconnected=on_reconnected,
        on_unrecoverable=on_unrecoverable,
    )

    try:
        session = await manager.start()
        print(f"\n{'='*50}")
        print(f"CDP Browser Orchestrator Running")
        print(f"{'='*50}")
        print(f"Browser: {session.get('Browser', 'unknown')}")
        print(f"WebSocket: {session.get('webSocketDebuggerUrl', 'N/A')}")
        print(f"State: {manager.state.name}")
        print(f"{'='*50}")
        print(f"\nTry closing the Chrome window manually.")
        print(f"The orchestrator will detect and auto-reconnect.\n")

        # Keep running until interrupted
        while True:
            await asyncio.sleep(1)

    except KeyboardInterrupt:
        print("\nStopping...")
    except BrowserUnrecoverableError as e:
        print(f"\nFatal: {e}")
        raise SystemExit(1)
    finally:
        await manager.stop()


if __name__ == "__main__":
    asyncio.run(main())
