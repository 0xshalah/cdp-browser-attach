"""CDP Browser Orchestrator — Production-grade CDP connection handling."""

from .browser_spawner import BrowserSpawner, ensure_browser_running
from .healthcheck import HealthMonitor
from .reconnect import ReconnectManager, BrowserUnrecoverableError

__all__ = [
    "BrowserSpawner",
    "ensure_browser_running",
    "HealthMonitor",
    "ReconnectManager",
    "BrowserUnrecoverableError",
]

__version__ = "1.0.0"
