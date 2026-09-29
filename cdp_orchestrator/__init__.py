"""CDP Browser Orchestrator — Production-grade CDP connection handling."""

from .browser_spawner import ensure_browser_running, BrowserLaunchError
from .healthcheck import HealthMonitor, ConnectionState
from .reconnect import ReconnectManager, ReconnectState, BrowserUnrecoverableError

__all__ = [
    "ensure_browser_running",
    "BrowserLaunchError",
    "HealthMonitor",
    "ConnectionState",
    "ReconnectManager",
    "ReconnectState",
    "BrowserUnrecoverableError",
]

__version__ = "1.0.0"
