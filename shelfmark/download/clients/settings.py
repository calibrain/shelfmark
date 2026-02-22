"""Shared download client settings registration."""

from contextlib import contextmanager
from typing import Any, Dict, Optional

from shelfmark.core.settings_registry import (
    register_settings,
    HeadingField,
    TextField,
    PasswordField,
    ActionButton,
    SelectField,
    TagListField,
)
from shelfmark.core.utils import normalize_http_url
from shelfmark.download.network import get_ssl_verify


# ==================== Test Connection Callbacks ====================

@contextmanager
def _transmission_session_verify_override(url: str):
    """Ensure transmission-rpc constructor uses the configured TLS verify mode."""
    verify = get_ssl_verify(url)
    if verify:
        yield
        return

    try:
        import transmission_rpc.client as transmission_rpc_client
    except Exception:
        yield
        return

    original_session_factory = transmission_rpc_client.requests.Session

    def _session_factory(*args: Any, **kwargs: Any) -> Any:
        session = original_session_factory(*args, **kwargs)
        session.verify = False
        return session

    transmission_rpc_client.requests.Session = _session_factory
    try:
        yield
    finally:
        transmission_rpc_client.requests.Session = original_session_factory


def _test_qbittorrent_connection(current_values: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Test the qBittorrent connection using current form values."""
    from shelfmark.core.config import config

    current_values = current_values or {}

    raw_url = current_values.get("QBITTORRENT_URL") or config.get("QBITTORRENT_URL", "")
    username = current_values.get("QBITTORRENT_USERNAME") or config.get("QBITTORRENT_USERNAME", "")
    password = current_values.get("QBITTORRENT_PASSWORD") or config.get("QBITTORRENT_PASSWORD", "")

    if not raw_url:
        return {"success": False, "message": "qBittorrent URL is required"}

    try:
        from qbittorrentapi import Client

        url = normalize_http_url(raw_url)
        if not url:
            return {"success": False, "message": "qBittorrent URL is invalid"}

        client = Client(host=url, username=username, password=password, VERIFY_WEBUI_CERTIFICATE=get_ssl_verify(url))
        client.auth_log_in()
        api_version = client.app.web_api_version
        return {"success": True, "message": f"Connected to qBittorrent (API v{api_version})"}
    except ImportError:
        return {"success": False, "message": "qbittorrent-api package not installed"}
    except Exception as e:
        return {"success": False, "message": f"Connection failed: {str(e)}"}


def _test_transmission_connection(current_values: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Test the Transmission connection using current form values."""
    from shelfmark.core.config import config
    from shelfmark.download.clients.torrent_utils import (
        parse_transmission_url,
    )

    current_values = current_values or {}

    raw_url = current_values.get("TRANSMISSION_URL") or config.get("TRANSMISSION_URL", "")
    username = current_values.get("TRANSMISSION_USERNAME") or config.get("TRANSMISSION_USERNAME", "")
    password = current_values.get("TRANSMISSION_PASSWORD") or config.get("TRANSMISSION_PASSWORD", "")

    if not raw_url:
        return {"success": False, "message": "Transmission URL is required"}

    url = normalize_http_url(raw_url)
    if not url:
        return {"success": False, "message": "Transmission URL is invalid"}

    try:
        from transmission_rpc import Client

        # Parse URL to extract host, port, and path
        protocol, host, port, path = parse_transmission_url(url)

        client_kwargs = {
            "host": host,
            "port": port,
            "path": path,
            "username": username if username else None,
            "password": password if password else None,
            "protocol": protocol,
        }
        try:
            with _transmission_session_verify_override(url):
                client = Client(**client_kwargs)
        except TypeError as e:
            if "protocol" not in str(e):
                raise
            client_kwargs.pop("protocol", None)
            with _transmission_session_verify_override(url):
                client = Client(**client_kwargs)
            if protocol == "https" and hasattr(client, "protocol"):
                try:
                    setattr(client, "protocol", protocol)
                except Exception:
                    pass

        # Keep session verify aligned for subsequent calls beyond constructor bootstrap.
        http_session = getattr(client, "_http_session", None)
        if http_session is not None:
            http_session.verify = get_ssl_verify(url)

        session = client.get_session()
        version = session.version
        return {"success": True, "message": f"Connected to Transmission {version}"}
    except ImportError:
        return {"success": False, "message": "transmission-rpc package not installed"}
    except Exception as e:
        return {"success": False, "message": f"Connection failed: {str(e)}"}


def _test_deluge_connection(current_values: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Test Deluge Web UI JSON-RPC connection using current form values."""
    from urllib.parse import urlparse

    import requests
    from shelfmark.core.config import config

    current_values = current_values or {}

    raw_host = current_values.get("DELUGE_HOST") or config.get("DELUGE_HOST", "localhost")
    raw_port = current_values.get("DELUGE_PORT") or config.get("DELUGE_PORT", "8112")
    password = current_values.get("DELUGE_PASSWORD") or config.get("DELUGE_PASSWORD", "")

    if not raw_host:
        return {"success": False, "message": "Deluge host is required"}
    if not password:
        return {"success": False, "message": "Deluge password is required"}

    raw_host = str(raw_host)
    raw_host = normalize_http_url(raw_host, strip_trailing_slash=False) if raw_host else ""
    if not raw_host:
        return {"success": False, "message": "Deluge host is invalid"}

    raw_port = str(raw_port or "8112")

    scheme = "http"
    base_path = ""
    host = raw_host
    port = int(raw_port) if raw_port.isdigit() else 8112

    # Allow DELUGE_HOST to be a full URL (e.g. http://deluge:8112)
    if raw_host.startswith(("http://", "https://")):
        parsed = urlparse(raw_host)
        scheme = parsed.scheme or "http"
        host = parsed.hostname or "localhost"
        if parsed.port is not None:
            port = parsed.port
        base_path = (parsed.path or "").rstrip("/")
    else:
        # Allow "host:port" in DELUGE_HOST for convenience.
        if ":" in raw_host and raw_host.count(":") == 1:
            host_part, port_part = raw_host.split(":", 1)
            if host_part and port_part.isdigit():
                host = host_part
                port = int(port_part)

    rpc_url = f"{scheme}://{host}:{port}{base_path}/json"

    def rpc_call(session: requests.Session, rpc_id: int, method: str, *params: Any) -> Any:
        payload = {"id": rpc_id, "method": method, "params": list(params)}
        resp = session.post(rpc_url, json=payload, timeout=15, verify=get_ssl_verify(rpc_url))
        resp.raise_for_status()
        data = resp.json()
        if data.get("error"):
            error = data["error"]
            if isinstance(error, dict):
                raise Exception(error.get("message") or str(error))
            raise Exception(str(error))
        return data.get("result")

    def get_daemon_version(session: requests.Session, rpc_id: int) -> Any:
        try:
            methods = rpc_call(session, rpc_id, "system.listMethods")
            if isinstance(methods, list) and "daemon.get_version" in methods:
                return rpc_call(session, rpc_id + 1, "daemon.get_version")
        except Exception:
            # Fall back to daemon.info to preserve existing behavior.
            pass

        return rpc_call(session, rpc_id + 1, "daemon.info")

    try:
        session = requests.Session()

        if rpc_call(session, 1, "auth.login", password) is not True:
            return {"success": False, "message": "Deluge Web UI authentication failed"}

        if rpc_call(session, 2, "web.connected") is not True:
            hosts = rpc_call(session, 3, "web.get_hosts") or []
            if not hosts:
                return {
                    "success": False,
                    "message": "Deluge Web UI isn't connected to Deluge core (no hosts configured). Add/connect a daemon in Deluge Web UI → Connection Manager.",
                }

            host_id = hosts[0][0]
            for entry in hosts:
                if isinstance(entry, list) and len(entry) >= 2 and entry[1] in {"127.0.0.1", "localhost"}:
                    host_id = entry[0]
                    break

            rpc_call(session, 4, "web.connect", host_id)

            if rpc_call(session, 5, "web.connected") is not True:
                return {
                    "success": False,
                    "message": "Deluge Web UI couldn't connect to Deluge core. Check Deluge Web UI → Connection Manager.",
                }

        version = get_daemon_version(session, 6)
        return {"success": True, "message": f"Connected to Deluge {version}"}

    except requests.exceptions.ConnectionError:
        return {"success": False, "message": "Could not connect to Deluge Web UI"}
    except requests.exceptions.Timeout:
        return {"success": False, "message": "Connection timed out"}
    except Exception as e:
        return {"success": False, "message": f"Connection failed: {str(e)}"}


def _test_rtorrent_connection(current_values: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Test the rTorrent connection using current form values."""
    from shelfmark.core.config import config
    import ssl
    from urllib.parse import urlparse
    from xmlrpc.client import SafeTransport, ServerProxy

    current_values = current_values or {}

    raw_url = current_values.get("RTORRENT_URL") or config.get("RTORRENT_URL", "")
    username = current_values.get("RTORRENT_USERNAME") or config.get("RTORRENT_USERNAME", "")
    password = current_values.get("RTORRENT_PASSWORD") or config.get("RTORRENT_PASSWORD", "")

    if not raw_url:
        return {"success": False, "message": "rTorrent URL is required"}

    url = normalize_http_url(raw_url)
    if not url:
        return {"success": False, "message": "rTorrent URL is invalid"}

    try:
        # Add HTTP auth to URL if credentials provided
        if username and password:
            parsed = urlparse(url)
            url = f"{parsed.scheme}://{username}:{password}@{parsed.netloc}{parsed.path}"

        rpc_url = url.rstrip("/")
        verify = get_ssl_verify(rpc_url)
        if rpc_url.startswith("https://") and not verify:
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            rpc = ServerProxy(rpc_url, transport=SafeTransport(context=ssl_context))
        else:
            rpc = ServerProxy(rpc_url)

        version = rpc.system.client_version()
        return {"success": True, "message": f"Connected to rTorrent {version}"}
    except Exception as e:
        return {"success": False, "message": f"Connection failed: {str(e)}"}


def _test_nzbget_connection(current_values: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Test the NZBGet connection using current form values."""
    import requests
    from shelfmark.core.config import config

    current_values = current_values or {}

    raw_url = current_values.get("NZBGET_URL") or config.get("NZBGET_URL", "")
    username = current_values.get("NZBGET_USERNAME") or config.get("NZBGET_USERNAME", "nzbget")
    password = current_values.get("NZBGET_PASSWORD") or config.get("NZBGET_PASSWORD", "")

    if not raw_url:
        return {"success": False, "message": "NZBGet URL is required"}

    url = normalize_http_url(raw_url)
    if not url:
        return {"success": False, "message": "NZBGet URL is invalid"}

    try:
        rpc_url = f"{url.rstrip('/')}/jsonrpc"
        payload = {"jsonrpc": "2.0", "method": "status", "params": [], "id": 1}
        response = requests.post(rpc_url, json=payload, auth=(username, password), timeout=30, verify=get_ssl_verify(rpc_url))
        response.raise_for_status()
        result = response.json()
        if "error" in result and result["error"]:
            raise Exception(result["error"].get("message", "RPC error"))
        version = result.get("result", {}).get("Version", "unknown")
        return {"success": True, "message": f"Connected to NZBGet {version}"}
    except requests.exceptions.ConnectionError:
        return {"success": False, "message": "Could not connect to NZBGet"}
    except requests.exceptions.Timeout:
        return {"success": False, "message": "Connection timed out"}
    except Exception as e:
        return {"success": False, "message": f"Connection failed: {str(e)}"}


def _test_sabnzbd_connection(current_values: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Test the SABnzbd connection using current form values."""
    import requests
    from shelfmark.core.config import config

    current_values = current_values or {}

    raw_url = current_values.get("SABNZBD_URL") or config.get("SABNZBD_URL", "")
    api_key = current_values.get("SABNZBD_API_KEY") or config.get("SABNZBD_API_KEY", "")

    if not raw_url:
        return {"success": False, "message": "SABnzbd URL is required"}

    url = normalize_http_url(raw_url)
    if not url:
        return {"success": False, "message": "SABnzbd URL is invalid"}
    if not api_key:
        return {"success": False, "message": "API key is required"}

    try:
        api_url = f"{url.rstrip('/')}/api"
        params = {"apikey": api_key, "mode": "version", "output": "json"}
        response = requests.get(api_url, params=params, timeout=30, verify=get_ssl_verify(api_url))
        response.raise_for_status()
        result = response.json()
        version = result.get("version", "unknown")
        return {"success": True, "message": f"Connected to SABnzbd {version}"}
    except requests.exceptions.ConnectionError:
        return {"success": False, "message": "Could not connect to SABnzbd"}
    except requests.exceptions.Timeout:
        return {"success": False, "message": "Connection timed out"}
    except Exception as e:
        return {"success": False, "message": f"Connection failed: {str(e)}"}


# ==================== Download Clients Tab ====================

@register_settings(
    name="prowlarr_clients",
    display_name="Download Clients",
    icon="cog",
    order=110,
)
def prowlarr_clients_settings():
    """Download client settings shared by external release sources."""
    return [
        # --- Torrent Client Selection ---
        HeadingField(
            key="torrent_heading",
            title="Torrent Client",
            description="Select and configure a torrent client for downloading torrent releases.",
        ),
        SelectField(
            key="PROWLARR_TORRENT_CLIENT",
            label="Torrent Client",
            description="Choose which torrent client to use",
            options=[
                {"value": "", "label": "None"},
                {"value": "qbittorrent", "label": "qBittorrent"},
                {"value": "transmission", "label": "Transmission"},
                {"value": "deluge", "label": "Deluge"},
                {"value": "rtorrent", "label": "rTorrent"},
            ],
            default="",
        ),

        # --- qBittorrent Settings ---
        TextField(
            key="QBITTORRENT_URL",
            label="qBittorrent URL",
            description="Web UI URL of your qBittorrent instance",
            placeholder="http://qbittorrent:8080",
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "qbittorrent"},
        ),
        TextField(
            key="QBITTORRENT_USERNAME",
            label="Username",
            description="qBittorrent Web UI username",
            placeholder="admin",
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "qbittorrent"},
        ),
        PasswordField(
            key="QBITTORRENT_PASSWORD",
            label="Password",
            description="qBittorrent Web UI password",
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "qbittorrent"},
        ),
        ActionButton(
            key="test_qbittorrent",
            label="Test Connection",
            description="Verify your qBittorrent configuration",
            style="primary",
            callback=_test_qbittorrent_connection,
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "qbittorrent"},
        ),
        TextField(
            key="QBITTORRENT_CATEGORY",
            label="Book Category",
            description="Category to assign to book downloads in qBittorrent",
            placeholder="books",
            default="books",
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "qbittorrent"},
        ),
        TextField(
            key="QBITTORRENT_CATEGORY_AUDIOBOOK",
            label="Audiobook Category",
            description="Category for audiobook downloads. Leave empty to use the book category.",
            placeholder="",
            default="",
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "qbittorrent"},
        ),
        TextField(
            key="QBITTORRENT_DOWNLOAD_DIR",
            label="Download Directory",
            description="Server-side directory where torrents are downloaded (optional, uses qBittorrent default if not specified)",
            placeholder="/downloads",
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "qbittorrent"},
        ),
        TagListField(
            key="QBITTORRENT_TAG",
            label="Tags",
            description="Tag(s) to assign to qBittorrent downloads. Leave empty for no tags.",
            placeholder="",
            default=[],
            normalize_urls=False,
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "qbittorrent"},
        ),

        # --- Transmission Settings ---
        TextField(
            key="TRANSMISSION_URL",
            label="Transmission URL",
            description="URL of your Transmission instance (use https:// for TLS)",
            placeholder="http://transmission:9091",
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "transmission"},
        ),
        TextField(
            key="TRANSMISSION_USERNAME",
            label="Username",
            description="Transmission RPC username (if authentication enabled)",
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "transmission"},
        ),
        PasswordField(
            key="TRANSMISSION_PASSWORD",
            label="Password",
            description="Transmission RPC password",
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "transmission"},
        ),
        ActionButton(
            key="test_transmission",
            label="Test Connection",
            description="Verify your Transmission configuration",
            style="primary",
            callback=_test_transmission_connection,
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "transmission"},
        ),
        TextField(
            key="TRANSMISSION_CATEGORY",
            label="Book Label",
            description="Label to assign to book downloads in Transmission",
            placeholder="books",
            default="books",
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "transmission"},
        ),
        TextField(
            key="TRANSMISSION_CATEGORY_AUDIOBOOK",
            label="Audiobook Label",
            description="Label for audiobook downloads. Leave empty to use the book label.",
            placeholder="",
            default="",
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "transmission"},
        ),
        TextField(
            key="TRANSMISSION_DOWNLOAD_DIR",
            label="Download Directory",
            description="Server-side directory where torrents are downloaded (optional, uses Transmission default if not specified)",
            placeholder="/downloads",
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "transmission"},
        ),

        # --- Deluge Settings ---
        TextField(
            key="DELUGE_HOST",
            label="Deluge Web UI Host/URL",
            description="Hostname/IP or full URL of your Deluge Web UI (deluge-web)",
            placeholder="http://deluge:8112",
            default="localhost",
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "deluge"},
        ),
        TextField(
            key="DELUGE_PORT",
            label="Deluge Web UI Port",
            description="Deluge Web UI port (default: 8112)",
            placeholder="8112",
            default="8112",
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "deluge"},
        ),
        PasswordField(
            key="DELUGE_PASSWORD",
            label="Password",
            description="Deluge Web UI password (default: deluge)",
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "deluge"},
        ),
        ActionButton(
            key="test_deluge",
            label="Test Connection",
            description="Verify your Deluge configuration",
            style="primary",
            callback=_test_deluge_connection,
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "deluge"},
        ),
        TextField(
            key="DELUGE_CATEGORY",
            label="Book Label",
            description="Label to assign to book downloads in Deluge",
            placeholder="books",
            default="books",
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "deluge"},
        ),
        TextField(
            key="DELUGE_CATEGORY_AUDIOBOOK",
            label="Audiobook Label",
            description="Label for audiobook downloads. Leave empty to use the book label.",
            placeholder="",
            default="",
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "deluge"},
        ),
        TextField(
            key="DELUGE_DOWNLOAD_DIR",
            label="Download Directory",
            description="Server-side directory where torrents are downloaded (optional, uses Deluge default if not specified)",
            placeholder="/downloads",
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "deluge"},
        ),

        # --- rTorrent Settings ---
        TextField(
            key="RTORRENT_URL",
            label="rTorrent URL",
            description="XML-RPC URL of your rTorrent instance",
            placeholder="http://rtorrent:6881/RPC2",
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "rtorrent"},
        ),
        TextField(
            key="RTORRENT_USERNAME",
            label="Username",
            description="HTTP Basic auth username (if authentication enabled)",
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "rtorrent"},
        ),
        PasswordField(
            key="RTORRENT_PASSWORD",
            label="Password",
            description="HTTP Basic auth password",
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "rtorrent"},
        ),
        ActionButton(
            key="test_rtorrent",
            label="Test Connection",
            description="Verify your rTorrent configuration",
            style="primary",
            callback=_test_rtorrent_connection,
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "rtorrent"},
        ),
        TextField(
            key="RTORRENT_LABEL",
            label="Book Label",
            description="Label to assign to book downloads in rTorrent",
            placeholder="cwabd",
            default="cwabd",
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "rtorrent"},
        ),
        TextField(
            key="RTORRENT_DOWNLOAD_DIR",
            label="Download Directory",
            description="Server-side directory where torrents are downloaded (optional, uses rTorrent default if not specified)",
            placeholder="/downloads",
            show_when={"field": "PROWLARR_TORRENT_CLIENT", "value": "rtorrent"},
        ),
        # Note: Torrent client download path must be mounted identically in both containers.
        # Torrents are always copied (not moved) to preserve seeding capability.

        # --- Usenet Client Selection ---
        HeadingField(
            key="usenet_heading",
            title="Usenet Client",
            description="Select and configure a usenet client for downloading NZB releases.",
        ),
        SelectField(
            key="PROWLARR_USENET_CLIENT",
            label="Usenet Client",
            description="Choose which usenet client to use",
            options=[
                {"value": "", "label": "None"},
                {"value": "nzbget", "label": "NZBGet"},
                {"value": "sabnzbd", "label": "SABnzbd"},
            ],
            default="",
        ),

        # --- NZBGet Settings ---
        TextField(
            key="NZBGET_URL",
            label="NZBGet URL",
            description="URL of your NZBGet instance",
            placeholder="http://nzbget:6789",
            show_when={"field": "PROWLARR_USENET_CLIENT", "value": "nzbget"},
        ),
        TextField(
            key="NZBGET_USERNAME",
            label="Username",
            description="NZBGet control username",
            placeholder="nzbget",
            default="nzbget",
            show_when={"field": "PROWLARR_USENET_CLIENT", "value": "nzbget"},
        ),
        PasswordField(
            key="NZBGET_PASSWORD",
            label="Password",
            description="NZBGet control password",
            show_when={"field": "PROWLARR_USENET_CLIENT", "value": "nzbget"},
        ),
        ActionButton(
            key="test_nzbget",
            label="Test Connection",
            description="Verify your NZBGet configuration",
            style="primary",
            callback=_test_nzbget_connection,
            show_when={"field": "PROWLARR_USENET_CLIENT", "value": "nzbget"},
        ),
        TextField(
            key="NZBGET_CATEGORY",
            label="Book Category",
            description="Category to assign to book downloads in NZBGet",
            placeholder="Books",
            default="Books",
            show_when={"field": "PROWLARR_USENET_CLIENT", "value": "nzbget"},
        ),
        TextField(
            key="NZBGET_CATEGORY_AUDIOBOOK",
            label="Audiobook Category",
            description="Category for audiobook downloads. Leave empty to use the book category.",
            placeholder="",
            default="",
            show_when={"field": "PROWLARR_USENET_CLIENT", "value": "nzbget"},
        ),

        # --- SABnzbd Settings ---
        TextField(
            key="SABNZBD_URL",
            label="SABnzbd URL",
            description="URL of your SABnzbd instance",
            placeholder="http://sabnzbd:8080",
            show_when={"field": "PROWLARR_USENET_CLIENT", "value": "sabnzbd"},
        ),
        PasswordField(
            key="SABNZBD_API_KEY",
            label="API Key",
            description="Found in SABnzbd: Config > General > API Key",
            show_when={"field": "PROWLARR_USENET_CLIENT", "value": "sabnzbd"},
        ),
        ActionButton(
            key="test_sabnzbd",
            label="Test Connection",
            description="Verify your SABnzbd configuration",
            style="primary",
            callback=_test_sabnzbd_connection,
            show_when={"field": "PROWLARR_USENET_CLIENT", "value": "sabnzbd"},
        ),
        TextField(
            key="SABNZBD_CATEGORY",
            label="Book Category",
            description="Category to assign to book downloads in SABnzbd",
            placeholder="books",
            default="books",
            show_when={"field": "PROWLARR_USENET_CLIENT", "value": "sabnzbd"},
        ),
        TextField(
            key="SABNZBD_CATEGORY_AUDIOBOOK",
            label="Audiobook Category",
            description="Category for audiobook downloads. Leave empty to use the book category.",
            placeholder="",
            default="",
            show_when={"field": "PROWLARR_USENET_CLIENT", "value": "sabnzbd"},
        ),
        # Note: Usenet client download path must be mounted identically in both containers.
        SelectField(
            key="PROWLARR_USENET_ACTION",
            label="NZB Completion Action",
            description="Move deletes the job from your usenet client after import; Copy keeps it in the client",
            options=[
                {"value": "move", "label": "Move"},
                {"value": "copy", "label": "Copy"},
            ],
            default="move",
            show_when={"field": "PROWLARR_USENET_CLIENT", "notEmpty": True},
        ),
    ]
