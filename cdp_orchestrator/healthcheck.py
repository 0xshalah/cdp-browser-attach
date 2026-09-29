"""Healthcheck — background heartbeat monitoring for CDP sessions."""

from __future__ import annotations

import asyncio
import logging
import time
from enum import Enum
from typing import Awaitable, Callable, Optional

import aiohttp

logger = logging.getLogger(__name__)

CDP_VERSION_URL = "http://127.0.0.1:9222/json/version"
DEFAULT_INTERVAL = 4.0


class ConnectionState(Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DISCONNECTED = "disconnected"


class HealthMonitor:
    """Non-blocking background heartbeat monitor for CDP sessions.

    Emits structured callbacks on state transitions without blocking
    the host agent's event loop.
    """

    def __init__(
        self,
        interval: float = DEFAULT_INTERVAL,
        on_disconnected: Optional[Callable[[str], Awaitable[None]]] = None,
        on_healthy: Optional[Callable[[], Awaitable[None]]] = None,
    ):
        self._interval = interval
        self._on_disconnected = on_disconnected
        self._on_healthy = on_healthy
        self._state = ConnectionState.HEALTHY
        self._stop_event: Optional[asyncio.Event] = None
        self._task: Optional[asyncio.Task] = None
        self._consecutive_failures = 0
        self._max_failures = 2

    @property
    def state(self) -> ConnectionState:
        return self._state

    async def start(self) -> None:
        """Start the background heartbeat loop."""
        if self._task and not self._task.done():
            logger.warning("HealthMonitor already running")
            return

        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._heartbeat_loop())
        logger.info("HealthMonitor started (interval: %.1fs)", self._interval)

    async def stop(self) -> None:
        """Gracefully stop the heartbeat loop."""
        if self._stop_event:
            self._stop_event.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except asyncio.TimeoutError:
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
        logger.info("HealthMonitor stopped")

    async def _heartbeat_loop(self) -> None:
        """Main heartbeat loop — runs until stop_event is set."""
        assert self._stop_event is not None

        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._interval,
                )
                # If wait() returns without timeout, stop_event was set
                break
            except asyncio.TimeoutError:
                pass  # Normal — time to ping

            await self._ping()

    async def _ping(self) -> None:
        """Single heartbeat ping."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    CDP_VERSION_URL,
                    timeout=aiohttp.ClientTimeout(total=3),
                ) as resp:
                    if resp.status == 200:
                        await resp.json()
                        await self._handle_success()
                        return
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
            logger.debug("Heartbeat ping failed: %s", e)

        await self._handle_failure()

    async def _handle_success(self) -> None:
        """Handle successful ping."""
        if self._state != ConnectionState.HEALTHY:
            logger.info("CDP connection restored")
            self._state = ConnectionState.HEALTHY
            if self._on_healthy:
                try:
                    await self._on_healthy()
                except Exception as e:
                    logger.error("Error in on_healthy callback: %s", e)

        self._consecutive_failures = 0

    async def _handle_failure(self) -> None:
        """Handle failed ping."""
        self._consecutive_failures += 1

        if self._consecutive_failures >= self._max_failures:
            if self._state != ConnectionState.DISCONNECTED:
                reason = (
                    f"CDP unreachable after {self._consecutive_failures} attempts"
                )
                logger.warning("Browser disconnected: %s", reason)
                self._state = ConnectionState.DISCONNECTED

                if self._on_disconnected:
                    try:
                        await self._on_disconnected(reason)
                    except Exception as e:
                        logger.error("Error in on_disconnected callback: %s", e)
        else:
            if self._state == ConnectionState.HEALTHY:
                self._state = ConnectionState.DEGRADED
                logger.warning(
                    "CDP degraded (%d/%d failures)",
                    self._consecutive_failures,
                    self._max_failures,
                )
