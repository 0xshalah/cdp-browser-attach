"""CDP Browser Orchestrator — Production-grade CDP connection handling."""

from .stealth_launcher import StealthLauncher, BrowserSpawnError
from .process_supervisor import (
    ProcessSupervisor,
    SupervisorState,
    CDPReconnectFailedError,
)
from .healthcheck import HealthMonitor, ConnectionState
from .reconnect import ReconnectManager, ReconnectState, BrowserUnrecoverableError
from .network_interceptor import NetworkInterceptor, InterceptorConfig, NetworkResponse
from .runtime_evaluator import RuntimeEvaluator
from .session_bridge import SessionBridge
from .api_sniffer import APISniffer, SniffedAPI

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
    "NetworkInterceptor",
    "InterceptorConfig",
    "NetworkResponse",
    "RuntimeEvaluator",
    "SessionBridge",
    "APISniffer",
    "SniffedAPI",
]

__version__ = "3.0.0"
