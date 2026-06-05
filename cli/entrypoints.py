"""CLI entry points for the installed package."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from collections.abc import Mapping, Sequence
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import uvicorn

from api.admin_urls import local_admin_url, local_proxy_root_url
from api.app import GracefulLifespanApp, create_app
from cli.process_registry import (
    kill_all_best_effort,
    kill_pid_tree_best_effort,
    register_pid,
    unregister_pid,
)
from config.paths import (
    config_dir_path,
    legacy_env_paths,
    managed_env_path,
    server_log_path,
)
from config.settings import Settings, get_settings

PROXY_PREFLIGHT_PATH = "/health"
PROXY_PREFLIGHT_TIMEOUT_SECONDS = 1.5
SERVER_GRACEFUL_SHUTDOWN_SECONDS = 5
PROXY_READY_TIMEOUT_SECONDS = 30.0
PROXY_READY_POLL_INTERVAL_SECONDS = 0.2

# DeepSeek per-tier routing for the consolidated `ds` command. deepseek-v4-max
# does not exist, so opus and sonnet share deepseek-v4-pro and are differentiated
# by reasoning effort (forwarded as output_config.effort by the proxy).
_DEEPSEEK_CLAUDE_CODE_MODEL_ENV = {
    "MODEL": "deepseek/deepseek-v4-pro",
    "MODEL_OPUS": "deepseek/deepseek-v4-pro",
    "MODEL_OPUS_EFFORT": "max",
    "MODEL_SONNET": "deepseek/deepseek-v4-pro",
    "MODEL_SONNET_EFFORT": "high",
    "MODEL_HAIKU": "deepseek/deepseek-v4-flash",
    "MODEL_HAIKU_EFFORT": "high",
}
_DEEPSEEK_CLAUDE_CODE_SUMMARY = (
    "DeepSeek Claude Code mapping: opus=deepseek-v4-pro@max, "
    "sonnet=deepseek-v4-pro@high, haiku=deepseek-v4-flash@high"
)


def _load_env_template() -> str:
    """Load the canonical root env template from package resources or source."""
    import importlib.resources

    packaged = importlib.resources.files("cli").joinpath("env.example")
    if packaged.is_file():
        return packaged.read_text("utf-8")

    source_template = Path(__file__).resolve().parents[1] / ".env.example"
    if source_template.is_file():
        return source_template.read_text(encoding="utf-8")

    raise FileNotFoundError("Could not find bundled or source .env.example template.")


def serve() -> None:
    """Start the FastAPI server (registered as `fcc-server` script)."""
    opened_admin_browser = False
    try:
        try:
            while True:
                _migrate_legacy_env_if_missing()
                settings = get_settings()
                if not _run_supervised_server(
                    settings, open_admin_browser=not opened_admin_browser
                ):
                    return
                opened_admin_browser = True
                get_settings.cache_clear()
        except KeyboardInterrupt:
            return
    finally:
        kill_all_best_effort()


def _apply_model_env(model_env: Mapping[str, str]) -> None:
    """Apply model routing env overrides for the current process."""
    for key, value in model_env.items():
        os.environ[key] = value


def _serve_with_model_env(model_env: Mapping[str, str], *, summary: str) -> None:
    """Apply model routing env overrides, then start the server."""
    _apply_model_env(model_env)
    print(summary)
    serve()


def serve_deepseek() -> None:
    """Start the FastAPI server with the DeepSeek model shorthand."""
    _serve_with_model_env(
        {"MODEL": "deepseek/deepseek-chat"},
        summary="Starting server with DeepSeek model: deepseek/deepseek-chat",
    )


def _await_proxy_ready(proxy_root_url: str) -> bool:
    """Poll the proxy health endpoint until reachable or the timeout elapses."""
    deadline = time.monotonic() + PROXY_READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if _preflight_proxy(proxy_root_url) is None:
            return True
        time.sleep(PROXY_READY_POLL_INTERVAL_SECONDS)
    return False


def _spawn_background_server(env: Mapping[str, str]) -> subprocess.Popen[bytes]:
    """Start ``serve()`` as a detached child with output redirected to the log.

    The child runs in its own process group so terminal Ctrl-C (used to interrupt
    Claude Code generations) does not also tear the proxy down; it is stopped
    explicitly when the foreground Claude Code process exits.
    """
    log_path = server_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-c", "from cli.entrypoints import serve; serve()"]
    creationflags = 0
    start_new_session = False
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        start_new_session = True
    with open(log_path, "ab") as log_handle:
        process = subprocess.Popen(
            command,
            stdout=log_handle,
            stderr=log_handle,
            env=dict(env),
            creationflags=creationflags,
            start_new_session=start_new_session,
        )
    register_pid(process.pid)
    return process


def _ensure_proxy(settings: Settings) -> subprocess.Popen[bytes] | None:
    """Reuse a reachable proxy, else spawn one. Return the owned process or None.

    Symmetric "first starter wins": whichever of ``ds`` / ``fcc-claude`` runs
    first starts and owns the proxy; the other reuses it. ``None`` means the
    proxy was reused (and so must not be torn down by the caller). On a startup
    race where a peer wins the port, the losing spawn is cleaned up and the
    peer's proxy is adopted instead of failing.
    """
    proxy_root_url = local_proxy_root_url(settings)
    if _preflight_proxy(proxy_root_url) is None:
        print(
            f"Using the proxy already running at {proxy_root_url} "
            "(its existing model routing applies)."
        )
        return None

    server_env = os.environ.copy()
    server_env.setdefault("FCC_OPEN_BROWSER", "0")
    print(f"Starting Free Claude Code proxy (logs: {server_log_path()})")
    server_process = _spawn_background_server(server_env)
    if _await_proxy_ready(proxy_root_url):
        return server_process

    # Our spawn never became ready: drop it, then adopt a proxy a concurrent
    # starter may have bound in the meantime; otherwise give up.
    kill_pid_tree_best_effort(server_process.pid)
    unregister_pid(server_process.pid)
    if _preflight_proxy(proxy_root_url) is None:
        print(f"Adopted a proxy started concurrently at {proxy_root_url}.")
        return None
    print(
        f"Proxy did not become ready at {proxy_root_url}; check {server_log_path()}.",
        file=sys.stderr,
    )
    raise SystemExit(1)


def serve_deepseek_and_launch_claude() -> None:
    """`ds`: start the DeepSeek-routed proxy (if needed) and launch Claude Code.

    Applies the DeepSeek per-tier mapping (opus=deepseek-v4-pro@max effort,
    sonnet=deepseek-v4-pro@high, haiku=deepseek-v4-flash@high), ensures a proxy
    is reachable, then runs Claude Code in the foreground. A proxy started by this
    command is stopped when Claude Code exits; an already-running proxy is reused.
    """
    _apply_model_env(_DEEPSEEK_CLAUDE_CODE_MODEL_ENV)
    print(_DEEPSEEK_CLAUDE_CODE_SUMMARY)

    settings = get_settings()
    server_process = _ensure_proxy(settings)
    try:
        launch_claude()
    finally:
        if server_process is not None:
            kill_pid_tree_best_effort(server_process.pid)
            unregister_pid(server_process.pid)


def _admin_browser_open_enabled() -> bool:
    """Whether to open /admin when the server becomes reachable (FCC_OPEN_BROWSER)."""

    raw = os.environ.get("FCC_OPEN_BROWSER", "true").strip().lower()
    return raw not in {"", "0", "false", "no"}


def _schedule_open_admin_browser(settings: Settings) -> None:
    """After /health succeeds, open the admin UI in the default browser (daemon thread)."""

    if not _admin_browser_open_enabled():
        return

    admin_url = local_admin_url(settings)
    proxy_root_url = local_proxy_root_url(settings)

    def open_when_ready() -> None:
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            if _preflight_proxy(proxy_root_url) is None:
                webbrowser.open(admin_url)
                return
            time.sleep(0.15)

    threading.Thread(
        target=open_when_ready, name="fcc-open-admin-browser", daemon=True
    ).start()


def _run_supervised_server(settings: Settings, *, open_admin_browser: bool) -> bool:
    """Run one uvicorn server instance; return whether admin requested restart."""

    restart_requested = False
    server_holder: dict[str, uvicorn.Server] = {}

    def request_restart() -> None:
        nonlocal restart_requested
        restart_requested = True
        if server := server_holder.get("server"):
            server.should_exit = True

    app = create_app(lifespan_enabled=False)
    app.state.admin_restart_callback = request_restart
    asgi_app = GracefulLifespanApp(app)
    config = uvicorn.Config(
        asgi_app,
        host=settings.host,
        port=settings.port,
        log_level="debug",
        timeout_graceful_shutdown=SERVER_GRACEFUL_SHUTDOWN_SECONDS,
    )
    server = uvicorn.Server(config)
    server_holder["server"] = server
    if open_admin_browser:
        _schedule_open_admin_browser(settings)
    server.run()
    return restart_requested


def init() -> None:
    """Scaffold config at ~/.fcc/.env (registered as `fcc-init`)."""
    config_dir = config_dir_path()
    env_file = managed_env_path()

    migrated_from = _migrate_legacy_env_if_missing()
    if migrated_from is not None:
        print(f"Config migrated from {migrated_from} to {env_file}")
        print(
            "Edit it to set your API keys and model preferences, then run: fcc-server"
        )
        return

    if env_file.exists():
        print(f"Config already exists at {env_file}")
        print("Delete it first if you want to reset to defaults.")
        return

    config_dir.mkdir(parents=True, exist_ok=True)
    template = _load_env_template()
    env_file.write_text(template, encoding="utf-8")
    print(f"Config created at {env_file}")
    print("Edit it to set your API keys and model preferences, then run: fcc-server")


def _migrate_legacy_env_if_missing() -> Path | None:
    """Copy a legacy user env into the managed config path when absent."""

    env_file = managed_env_path()
    if env_file.exists():
        return None

    # TODO: Remove after the ~/.fcc/.env migration has had a release cycle.
    for legacy_env in legacy_env_paths():
        if not legacy_env.is_file():
            continue
        env_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(legacy_env, env_file)
        return legacy_env

    return None


# These env vars mark a running Claude Code session. Inheriting them causes a
# child `claude` process to think it is nested inside an existing session and
# switch to --print (non-interactive) mode, breaking interactive `ds` launches.
_CLAUDE_SESSION_ENV_KEYS: frozenset[str] = frozenset(
    {
        "CLAUDECODE",
        "CLAUDE_CODE_ENTRYPOINT",
        "CLAUDE_CODE_SESSION_ID",
        "CLAUDE_CODE_EXECPATH",
        "CLAUDE_CODE_TMPDIR",
        "AI_AGENT",
    }
)


def _claude_child_env(
    settings: Settings, base_env: Mapping[str, str]
) -> dict[str, str]:
    """Return a Claude Code environment that targets this proxy."""

    env = {
        key: value
        for key, value in base_env.items()
        if not key.startswith("ANTHROPIC_") and key not in _CLAUDE_SESSION_ENV_KEYS
    }
    env.pop("ANTHROPIC_API_KEY", None)
    env["ANTHROPIC_BASE_URL"] = local_proxy_root_url(settings)
    env["CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY"] = "1"
    env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = "190000"
    if token := settings.anthropic_auth_token.strip():
        env["ANTHROPIC_AUTH_TOKEN"] = token
    return env


def _preflight_proxy(proxy_root_url: str) -> str | None:
    """Return an error message when the local proxy health check is unreachable."""

    url = f"{proxy_root_url.rstrip('/')}{PROXY_PREFLIGHT_PATH}"
    request = Request(url, method="GET")
    try:
        with urlopen(request, timeout=PROXY_PREFLIGHT_TIMEOUT_SECONDS) as response:
            status_code = response.getcode()
    except HTTPError as exc:
        return f"returned HTTP {exc.code}"
    except URLError as exc:
        return str(exc.reason)
    except OSError as exc:
        return str(exc)

    if not 200 <= status_code < 300:
        return f"returned HTTP {status_code}"
    return None


def launch_claude(argv: Sequence[str] | None = None) -> None:
    """Launch Claude Code against the proxy, starting one if none is running.

    Symmetric with ``ds``: if a proxy is already reachable it is reused,
    otherwise one is started here and owned for the lifetime of this Claude Code
    session (torn down when it exits).
    """

    settings = get_settings()
    owned_proxy = _ensure_proxy(settings)
    try:
        _exec_claude(settings, argv)
    finally:
        if owned_proxy is not None:
            kill_pid_tree_best_effort(owned_proxy.pid)
            unregister_pid(owned_proxy.pid)


def _exec_claude(settings: Settings, argv: Sequence[str] | None) -> None:
    """Run Claude Code in the foreground (proxy reachability already ensured)."""

    args = list(sys.argv[1:] if argv is None else argv)
    claude_command = shutil.which(settings.claude_cli_bin)
    if claude_command is None:
        print(
            f"Could not find Claude Code command: {settings.claude_cli_bin}",
            file=sys.stderr,
        )
        print(
            "Install Claude Code with: npm install -g @anthropic-ai/claude-code",
            file=sys.stderr,
        )
        raise SystemExit(127)

    command = [claude_command, *args]
    env = _claude_child_env(settings, os.environ)
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(command, env=env)
        if process.pid:
            register_pid(process.pid)
        return_code = process.wait()
    except FileNotFoundError:
        print(
            f"Could not find Claude Code command: {settings.claude_cli_bin}",
            file=sys.stderr,
        )
        print(
            "Install Claude Code with: npm install -g @anthropic-ai/claude-code",
            file=sys.stderr,
        )
        raise SystemExit(127) from None
    except KeyboardInterrupt:
        if process is not None and process.pid:
            kill_pid_tree_best_effort(process.pid)
            process.wait()
        raise
    finally:
        if process is not None and process.pid:
            unregister_pid(process.pid)

    raise SystemExit(return_code)
