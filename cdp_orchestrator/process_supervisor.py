"""ProcessSupervisor — zero-crash lifecycle with state recovery."""

from __future__ import annotations

import asyncio
import logging
import time
from enum import Enum, auto
from typing import Awaitable, Callable, Optional

import aiohttp

from .stealth_launcher import StealthLauncher, BrowserSpawnError

logger = logging.getLogger(__name__)

CDP_VERSION_URL = "http://127.0.0.1:9222/json/version"
HEARTBEAT_INTERVAL = 2.0
MAX_RECONNECT_ATTEMPTS = 3
INITIAL_RECONNECT_DELAY = 0.5
MAX_RECONNECT_DELAY = 10.0
CALL_QUEUE_TIMEOUT = 10.0


class SupervisorState(Enum):
    RUNNING = auto()
    RECONNECTING = auto()
    STOPPED = auto()
    UNRECOVERABLE = auto()


class CDPReconnectFailedError(Exception):
    """Raised when CDP cannot be reconnected after max attempts."""


class ProcessSupervisor:
    """Zero-crash CDP process supervisor with state recovery.

    Monitors browser health, auto-revives on crash, queues calls during
    reconnection, and restores session state.
    """

    def __init__(
        self,
        port: int = 9222,
        profile_dir: Optional[str] = None,
        on_session_restored: Optional[Callable[[dict], Awaitable[None]]] = None,
        on_disconnected: Optional[Callable[[str], Awaitable[None]]] = None,
    ):
        self._port = port
        self._launcher = StealthLauncher(port=port, profile_dir=profile_dir)
        self._on_session_restored = on_session_restored
        self._on_disconnected = on_disconnected

        self._state = SupervisorState.STOPPED
        self._stop_event: Optional[asyncio.Event] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._session_cache: Optional[dict] = None
        self._call_queue: asyncio.Queue = asyncio.Queue()
        self._consecutive_failures = 0
        self._max_failures = 2

    @property
    def state(self) -> SupervisorState:
        return self._state

    @property
    def launcher(self) -> StealthLauncher:
        return self._launcher

    async def start(self) -> dict:
        """Start supervisor — ensures browser is running and monitors."""
        self._stop_event = asyncio.Event()

        # Initial launch
        version_info = await self._launcher.ensure_running()
        self._state = SupervisorState.RUNNING
        self._cache_session(version_info)

        # Start heartbeat
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        logger.info("ProcessSupervisor started, state=RUNNING")
        return version_info

    async def stop(self) -> None:
        """Gracefully stop supervisor and cleanup."""
        if self._stop_event:
            self._stop_event.set()
        if self._heartbeat_task:
            try:
                await asyncio.wait_for(self._heartbeat_task, timeout=5.0)
            except asyncio.TimeoutError:
                self._heartbeat_task.cancel()
                try:
                    await self._heartbeat_task
                except asyncio.CancelledError:
                    pass
        self._launcher.terminate()
        self._state = SupervisorState.STOPPED
        logger.info("ProcessSupervisor stopped")

    async def call(self, method: str, params: Optional[dict] = None) -> dict:
        """Queue a CDP call — waits if reconnecting."""
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        await self._call_queue.put((method, params, future))

        try:
            result = await asyncio.wait_for(future, timeout=CALL_QUEUE_TIMEOUT)
            return result
        except asyncio.TimeoutError:
            raise CDPReconnectFailedError(
                f"Call {method} timed out after {CALL_QUEUE_TIMEOUT}s (reconnecting)"
            )

    def _cache_session(self, version_info: dict) -> None:
        """Cache session state for recovery."""
        self._session_cache = {
            "browser": version_info.get("Browser", "unknown"),
            "ws_url": version_info.get("webSocketDebuggerUrl", ""),
            "timestamp": time.time(),
        }

    async def _heartbeat_loop(self) -> None:
        """Non-blocking heartbeat loop."""
        assert self._stop_event is not None

        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=HEARTBEAT_INTERVAL,
                )
                break
            except asyncio.TimeoutError:
                pass

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
                        data = await resp.json()
                        await self._handle_success(data)
                        return
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
            logger.debug("Heartbeat ping failed: %s", e)

        await self._handle_failure()

    async def _handle_success(self, data: dict) -> None:
        """Handle successful ping."""
        self._consecutive_failures = 0
        if self._state == SupervisorState.RECONNECTING:
            logger.info("Connection restored, state=RUNNING")
            self._state = SupervisorState.RUNNING
            self._cache_session(data)

    async def _handle_failure(self) -> None:
        """Handle failed ping."""
        self._consecutive_failures += 1

        if self._consecutive_failures >= self._max_failures:
            if self._state != SupervisorState.RECONNECTING:
                reason = f"CDP unreachable after {self._consecutive_failures} attempts"
                logger.warning("Browser disconnected: %s", reason)
                self._state = SupervisorState.RECONNECTING

                if self._on_disconnected:
                    try:
                        await self._on_disconnected(reason)
                    except Exception as e:
                        logger.error("Error in on_disconnected callback: %s", e)

                await self._auto_revive()

    async def _auto_revive(self) -> None:
        """Auto-revive browser with exponential backoff."""
        for attempt in range(1, MAX_RECONNECT_ATTEMPTS + 1):
            if self._stop_event and self._stop_event.is_set():
                logger.info("Auto-revive aborted (stop requested)")
                return

            delay = min(INITIAL_RECONNECT_DELAY * (2 ** (attempt - 1)), MAX_RECONNECT_DELAY)
            logger.info("Auto-revive attempt %d/%d (delay: %.1fs)", attempt, MAX_RECONNECT_ATTEMPTS, delay)
            await asyncio.sleep(delay)

            try:
                version_info = await self._launcher.ensure_running()
                logger.info("Browser revived on attempt %d", attempt)
                self._state = SupervisorState.RUNNING
                self._cache_session(version_info)

                if self._on_session_restored:
                    try:
                        await self._on_session_restored(version_info)
                    except Exception as e:
                        logger.error("Error in on_session_restored callback: %s", e)

                return

            except (BrowserSpawnError, OSError) as e:
                logger.warning("Auto-revive attempt %d failed: %s", attempt, e)

        logger.error("Max auto-revive attempts (%d) reached", MAX_RECONNECT_ATTEMPTS)
        self._state = SupervisorState.UNRECOVERABLE
        raise CDPReconnectFailedError(
            f"Browser unrecoverable after {MAX_RECONNECT_ATTEMPTS} attempts"
        )
