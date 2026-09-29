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
from .tab_manager import TabManager
from .form_filler import FormFiller
from .network_monitor import NetworkMonitor
from .inbox_poller import InboxPoller, EmailMessage
from .turnstile_handler import TurnstileHandler
from .dom_observer import DOMObserver
from .form_submitter import FormSubmitter, SubmissionResult, FormError, NetworkCapture

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
    "TabManager",
    "FormFiller",
    "NetworkMonitor",
    "InboxPoller",
    "EmailMessage",
    "TurnstileHandler",
    "DOMObserver",
    "FormSubmitter",
    "SubmissionResult",
    "FormError",
    "NetworkCapture",
]

__version__ = "4.1.0"
