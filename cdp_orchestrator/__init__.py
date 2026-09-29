"""CDP Browser Orchestrator — Production-grade CDP connection handling."""

from .stealth_launcher import StealthLauncher, BrowserSpawnError
from .process_supervisor import (
    ProcessSupervisor,
    SupervisorState,
    CDPReconnectFailedError,
)
from .healthcheck import HealthMonitor, ConnectionState
from .reconnect import ReconnectManager, ReconnectState, BrowserUnrecoverableError

__all__ = [
    "StealthLauncher",
    "BrowserSpawnError",
    "ProcessSupervisor",
    "SupervisorState",
    "CDPReconnectFailedError",
    "HealthMonitor",
    "ConnectionState",
    "ReconnectManager",
    "ReconnectState",
    "BrowserUnrecoverableError",
]

__version__ = "2.0.0"
