"""Runtime driver abstraction for VPS-PANEL.

LocalDriver  — plain subprocess sandbox (Termux / bare metal). [IMPLEMENTED]
DockerDriver — per-app containers with cgroups quotas (VPS). [STUB — same interface]
"""
from abc import ABC, abstractmethod


class RuntimeDriver(ABC):
    @abstractmethod
    def start(self, app: dict) -> bool:
        """Start the app. Returns True on success. Writes logs itself."""

    @abstractmethod
    def stop(self, app: dict) -> bool:
        """Stop the app gracefully (kill after timeout)."""

    @abstractmethod
    def is_running(self, app_id: str) -> bool:
        """Is the app's process currently alive?"""

    @abstractmethod
    def get_usage(self, app_id: str) -> dict:
        """Live usage: {running, cpu_pct, ram_mb, uptime_s}."""

    @abstractmethod
    def read_logs(self, app_id: str, limit: int = 200, search: str = None) -> str:
        """Tail of app log."""

    @abstractmethod
    def get_user_usage(self, username: str) -> dict:
        """Aggregated usage of all apps of a user: {ram_mb, cpu_pct, disk_mb, apps, running}."""

    @abstractmethod
    def enforce_quotas(self):
        """Called by monitor loop — stop heaviest app when a user exceeds quota."""

    @abstractmethod
    def run_shell(self, app_id: str, cmd: str, timeout: int = 10) -> dict:
        """Run one shell command in the app sandbox (used by non-WS terminal fallback)."""

    @abstractmethod
    def open_pty(self, app_id: str):
        """Return a PTY session object for interactive terminal (WebSocket)."""

    @abstractmethod
    def create_venv(self, app_id: str):
        """Install app dependencies in background (venv/npm)."""

    @abstractmethod
    def delete_files(self, app_id: str):
        """Remove sandbox dir + logs."""

    @abstractmethod
    def allocate_port(self) -> int:
        """Ask the OS for a free high port."""


class DockerDriver(RuntimeDriver):
    """VPS (Docker) driver — STUB. Same interface, implemented at VPS deploy phase.
    Container per app: docker run -d --cpus --memory --pids-limit, port mapping,
    docker exec for terminal, docker stats for usage, volumes for app dir.
    """
    def __init__(self):
        raise NotImplementedError("DockerDriver lands at VPS deploy phase (interface only).")

    def start(self, app): ...
    def stop(self, app): ...
    def is_running(self, app_id): ...
    def get_usage(self, app_id): ...
    def read_logs(self, app_id, limit=200, search=None): ...
    def get_user_usage(self, username): ...
    def enforce_quotas(self): ...
    def run_shell(self, app_id, cmd, timeout=10): ...
    def open_pty(self, app_id): ...
    def create_venv(self, app_id): ...
    def delete_files(self, app_id): ...
    def allocate_port(self): ...


def get_driver():
    """Select driver: VPSPANEL_DRIVER=local|docker (default local)."""
    import os
    name = os.environ.get("VPSPANEL_DRIVER", "local")
    if name == "docker":
        return DockerDriver()
    from runtime.local import LocalDriver
    return LocalDriver()