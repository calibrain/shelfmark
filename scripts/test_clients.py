#!/usr/bin/env python3
"""
Test script for download client implementations.

Usage:
    1. Start the test stack:
       docker compose -f docker-compose.test-clients.yml up -d

    2. Wait for containers to initialize (first run takes ~30s)

    3. Run this script to verify clients are accessible:
       python scripts/test_clients.py

    4. Access cwabd at http://localhost:8084
       - Go to Settings > Prowlarr > Download Clients
       - Select a client from the dropdown
       - Click "Test Connection" to verify

Web UIs:
    - cwabd:        http://localhost:8084
    - qBittorrent:  http://localhost:8080
    - Transmission: http://localhost:9091
    - Deluge:       http://localhost:8112
    - NZBGet:       http://localhost:6789
    - SABnzbd:      http://localhost:8085
    - rTorrent:     http://localhost:8000 (web ui http://localhost:8089 via ruTorrent)

Prerequisites (for running this script locally):
    pip install requests transmission-rpc qbittorrent-api

First-Time Setup:
    qBittorrent:
        - Check container logs for temporary password: docker logs test-qbittorrent
        - Login at http://localhost:8080, change password to something known
        - Default username is 'admin'

    Transmission:
        - No setup needed, credentials pre-configured (admin/admin)

    Deluge:
        - Access Web UI at http://localhost:8112 (default password: deluge)

    NZBGet:
        - No setup needed, credentials pre-configured (admin/admin)

    SABnzbd:
        - Complete the setup wizard at http://localhost:8085
        - API key will be auto-detected by this script
        - In cwabd, copy API key from SABnzbd Config > General
"""

import sys
import time
from xmlrpc import client

# Test configuration - matches docker-compose.test-clients.yml
CONFIG = {
    # Usenet clients
    "nzbget": {
        "url": "http://localhost:6789",
        "username": "admin",
        "password": "admin",
    },
    "sabnzbd": {
        "url": "http://localhost:8085",
        "api_key": None,  # Will be read from config on first run
    },
    # Torrent clients
    "qbittorrent": {
        "url": "http://localhost:8080",
        "username": "admin",
        "password": "5NCngsHXm",  # Temp password from: docker logs test-qbittorrent | grep password
    },
    "transmission": {
        "url": "http://localhost:9091",
        "username": "admin",
        "password": "admin",
    },
    "deluge": {
        "url": "http://localhost:8112",
        "password": "deluge",
    },
    "rtorrent": {
        "url": "http://localhost:8000/RPC2",
    },
}

# Test magnet link (Ubuntu ISO - legal, small metadata)
TEST_MAGNET = "magnet:?xt=urn:btih:3b245504cf5f11bbdbe1201cea6a6bf45aee1bc0&dn=ubuntu-22.04.3-live-server-amd64.iso"


def test_nzbget():
    """Test NZBGet connection."""
    import requests

    print("\n" + "=" * 50)
    print("Testing NZBGet")
    print("=" * 50)

    url = CONFIG["nzbget"]["url"]
    username = CONFIG["nzbget"]["username"]
    password = CONFIG["nzbget"]["password"]

    try:
        # Test connection via JSON-RPC
        rpc_url = f"{url}/jsonrpc"
        response = requests.post(
            rpc_url,
            json={"method": "version", "params": []},
            auth=(username, password),
            timeout=10,
        )
        response.raise_for_status()
        result = response.json()
        version = result.get("result", "unknown")
        print(f"  Connected to NZBGet {version}")

        # Test status
        response = requests.post(
            rpc_url,
            json={"method": "status", "params": []},
            auth=(username, password),
            timeout=10,
        )
        status = response.json().get("result", {})
        print(f"  Server state: {'Paused' if status.get('ServerPaused') else 'Running'}")
        print(f"  Downloads in queue: {status.get('DownloadedSizeMB', 0)} MB downloaded")

        print("  SUCCESS: NZBGet is working!")
        return True

    except requests.exceptions.ConnectionError:
        print("  ERROR: Could not connect to NZBGet")
        print("  Is the container running? docker ps | grep nzbget")
        return False
    except Exception as e:
        print(f"  ERROR: {e}")
        return False


def test_sabnzbd():
    """Test SABnzbd connection."""
    import requests

    print("\n" + "=" * 50)
    print("Testing SABnzbd")
    print("=" * 50)

    url = CONFIG["sabnzbd"]["url"]
    api_key = CONFIG["sabnzbd"]["api_key"]

    # Try to get API key from config if not set
    if not api_key:
        try:
            import os
            ini_path = ".local/test-clients/sabnzbd/config/sabnzbd.ini"
            if os.path.exists(ini_path):
                with open(ini_path) as f:
                    for line in f:
                        if line.startswith("api_key"):
                            api_key = line.split("=")[1].strip()
                            print(f"  Found API key in config: {api_key[:8]}...")
                            break
        except Exception as e:
            print(f"  Could not read API key from config: {e}")

    if not api_key:
        print("  ERROR: No API key configured")
        print("  Please access http://localhost:8085 and complete initial setup")
        print("  Then copy the API key from Config > General")
        return False

    try:
        # Test connection
        response = requests.get(
            f"{url}/api",
            params={"apikey": api_key, "mode": "version", "output": "json"},
            timeout=10,
        )
        response.raise_for_status()
        result = response.json()
        version = result.get("version", "unknown")
        print(f"  Connected to SABnzbd {version}")

        # Test queue status
        response = requests.get(
            f"{url}/api",
            params={"apikey": api_key, "mode": "queue", "output": "json"},
            timeout=10,
        )
        queue = response.json().get("queue", {})
        print(f"  Queue status: {queue.get('status', 'unknown')}")
        print(f"  Items in queue: {len(queue.get('slots', []))}")

        print("  SUCCESS: SABnzbd is working!")
        return True

    except requests.exceptions.ConnectionError:
        print("  ERROR: Could not connect to SABnzbd")
        print("  Is the container running? docker ps | grep sabnzbd")
        return False
    except Exception as e:
        print(f"  ERROR: {e}")
        return False


def test_qbittorrent():
    """Test qBittorrent connection."""
    print("\n" + "=" * 50)
    print("Testing qBittorrent")
    print("=" * 50)

    try:
        import qbittorrentapi

        url = CONFIG["qbittorrent"]["url"]
        username = CONFIG["qbittorrent"]["username"]
        password = CONFIG["qbittorrent"]["password"]

        # Parse URL for host/port
        from urllib.parse import urlparse
        parsed = urlparse(url)

        client = qbittorrentapi.Client(
            host=parsed.hostname,
            port=parsed.port or 8080,
            username=username,
            password=password,
        )

        # Test connection
        client.auth_log_in()
        version = client.app.version
        print(f"  Connected to qBittorrent {version}")

        # Get torrent list
        torrents = client.torrents_info()
        print(f"  Active torrents: {len(torrents)}")

        # Test adding a torrent (then remove it)
        print("  Testing add/remove torrent...")
        result = client.torrents_add(urls=TEST_MAGNET, is_paused=True)
        if result == "Ok.":
            # Wait a moment for it to be added
            time.sleep(1)
            torrents = client.torrents_info()
            if torrents:
                test_torrent = torrents[-1]  # Most recently added
                print(f"  Added test torrent: {test_torrent.name[:50]}...")
                print(f"  Status: {test_torrent.state}")

                # Remove it
                client.torrents_delete(torrent_hashes=test_torrent.hash, delete_files=True)
                print("  Removed test torrent")
        else:
            print(f"  Add result: {result}")

        print("  SUCCESS: qBittorrent is working!")
        return True

    except ImportError:
        print("  ERROR: qbittorrent-api not installed")
        print("  Run: pip install qbittorrent-api")
        return False
    except Exception as e:
        print(f"  ERROR: {e}")
        if "Forbidden" in str(e) or "401" in str(e):
            print("\n  Authentication failed. Check password:")
            print("  1. docker logs test-qbittorrent | grep password")
            print("  2. Login to http://localhost:8080 and set a known password")
        return False


def test_transmission():
    """Test Transmission connection."""
    print("\n" + "=" * 50)
    print("Testing Transmission")
    print("=" * 50)

    try:
        from transmission_rpc import Client
        from urllib.parse import urlparse

        url = CONFIG["transmission"]["url"]
        parsed = urlparse(url)

        client = Client(
            host=parsed.hostname,
            port=parsed.port or 9091,
            username=CONFIG["transmission"]["username"],
            password=CONFIG["transmission"]["password"],
        )

        # Test connection
        session = client.get_session()
        print(f"  Connected to Transmission {session.version}")

        # Get torrent list
        torrents = client.get_torrents()
        print(f"  Active torrents: {len(torrents)}")

        # Test adding a torrent (then remove it)
        print("  Testing add/remove torrent...")
        torrent = client.add_torrent(TEST_MAGNET, paused=True)
        print(f"  Added test torrent: {torrent.name[:50]}...")

        # Get status
        status = client.get_torrent(torrent.id)
        print(f"  Status: {status.status} ({status.percent_done * 100:.1f}%)")

        # Remove it
        client.remove_torrent(torrent.id, delete_data=True)
        print("  Removed test torrent")

        print("  SUCCESS: Transmission is working!")
        return True

    except ImportError:
        print("  ERROR: transmission-rpc not installed")
        print("  Run: pip install transmission-rpc")
        return False
    except Exception as e:
        print(f"  ERROR: {e}")
        return False


def test_deluge():
    """Test Deluge Web UI (JSON-RPC) connection."""
    import requests

    print("\n" + "=" * 50)
    print("Testing Deluge")
    print("=" * 50)

    base_url = CONFIG["deluge"]["url"].rstrip("/")
    password = CONFIG["deluge"]["password"]
    rpc_url = f"{base_url}/json"

    def rpc_call(session: requests.Session, rpc_id: int, method: str, *params):
        payload = {"id": rpc_id, "method": method, "params": list(params)}
        resp = session.post(rpc_url, json=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("error"):
            err = data["error"]
            if isinstance(err, dict):
                raise Exception(err.get("message") or str(err))
            raise Exception(str(err))
        return data.get("result")

    try:
        session = requests.Session()

        # Authenticate to Deluge Web
        if rpc_call(session, 1, "auth.login", password) is not True:
            raise Exception("Authentication failed (check Deluge Web UI password)")

        # Ensure Deluge Web is connected to a daemon
        if rpc_call(session, 2, "web.connected") is not True:
            hosts = rpc_call(session, 3, "web.get_hosts") or []
            if not hosts:
                raise Exception(
                    "Deluge Web UI isn't connected to Deluge core (no hosts configured). "
                    "Add/connect a daemon in Deluge Web UI → Connection Manager."
                )

            host_id = hosts[0][0]
            for entry in hosts:
                if isinstance(entry, list) and len(entry) >= 2 and entry[1] in {"127.0.0.1", "localhost"}:
                    host_id = entry[0]
                    break

            rpc_call(session, 4, "web.connect", host_id)

            if rpc_call(session, 5, "web.connected") is not True:
                raise Exception(
                    "Deluge Web UI couldn't connect to Deluge core. "
                    "Check Deluge Web UI → Connection Manager."
                )

        version = rpc_call(session, 6, "daemon.info")
        print(f"  Connected to Deluge {version}")

        torrents = rpc_call(session, 7, "core.get_torrents_status", {}, ["name"]) or {}
        print(f"  Active torrents: {len(torrents)}")

        # Test adding a torrent (then remove it)
        print("  Testing add/remove torrent...")
        torrent_id = rpc_call(session, 8, "core.add_torrent_magnet", TEST_MAGNET, {"add_paused": True})

        if torrent_id:
            torrent_id = str(torrent_id)
            print(f"  Added test torrent: {torrent_id[:20]}...")

            status = rpc_call(session, 9, "core.get_torrent_status", torrent_id, ["state", "progress"]) or {}
            state = status.get("state", "unknown") if isinstance(status, dict) else "unknown"
            progress = status.get("progress", 0) if isinstance(status, dict) else 0
            print(f"  Status: {state} ({progress:.1f}%)")

            rpc_call(session, 10, "core.remove_torrent", torrent_id, True)
            print("  Removed test torrent")
        else:
            print("  WARNING: Could not add test torrent")

        print("  SUCCESS: Deluge is working!")
        return True

    except requests.exceptions.ConnectionError:
        print("  ERROR: Could not connect to Deluge Web UI")
        print("  Is the container running? docker ps | grep deluge")
        return False
    except requests.exceptions.Timeout:
        print("  ERROR: Deluge Web UI connection timed out")
        return False
    except Exception as e:
        print(f"  ERROR: {e}")
        if "auth" in str(e).lower() or "login" in str(e).lower():
            print("  Check Deluge Web UI password (default: deluge)")
        return False

def test_rtorrent():
    """Test rTorrent connection."""
    print("\n" + "=" * 50)
    print("Testing rTorrent")
    print("=" * 50)

    try:
        import xmlrpc.client

        url = "http://localhost:8000/RPC2"
        client = xmlrpc.client.ServerProxy(url)

        # Test connection
        version = client.system.library_version()
        print(f"  Connected to rTorrent {version}")

        # default download directory test
        default_dir = client.directory.default()
        print(f"  Default download directory: {default_dir}")

        # Get torrent list
        torrents = client.download_list()
        print(f"  Active torrents: {len(torrents)}")

        # Test adding a torrent (then remove it)
        print("  Testing add/remove torrent...")

        label = "automated"

        commands = []
        if label:
            commands.append(f"d.custom1.set={label}")

        download_dir = "/downloads"
        if download_dir:
            commands.append(f"d.directory_base.set={download_dir}")

        # rtorrent is weird in that it doesn't return the torrent ID/hash on add
        client.load.start("", TEST_MAGNET, ";".join(commands))
        
        # but we know that it is 3b245504cf5f11bbdbe1201cea6a6bf45aee1bc0 from the magnet link
        torrent_id = "3B245504CF5F11BBDBE1201CEA6A6BF45AEE1BC0" # rtorrent uses uppercase hashes
        print(f"  Added test torrent: {torrent_id}")

        torrents = client.download_list()
        print(f"  Active torrents: {len(torrents)}")        

        torrent_list = client.d.multicall.filtered(
            "",
            "default",
            f"equal={{d.hash=,cat={torrent_id}}}"
            "d.hash=",
            "d.state=",
            "d.completed_bytes=",
            "d.size_bytes=",
            "d.down.rate=",
            "d.up.rate=",
            "d.custom1=",
            "d.complete=",
        )
        torrent = torrent_list[0]

        if not torrent:
            print("  ERROR: Could not find added torrent in list")
            return False
        
        # let's test the base path call
        details = client.d.multicall.filtered(
            "",
            "default",
            f"equal=d.hash=,cat={torrent_id}",
            "d.base_path=",
        )

        base_path = details[0][0] if details else None

        print(f"  Base path: {base_path}")
        client.d.erase(torrent_id)
        print("  Removed test torrent")

        print("  SUCCESS: rTorrent is working!")
        return True

    except ImportError:
        print("  ERROR: xmlrpc.client not available")
        return False
    except Exception as e:
        print(f"  ERROR: {e}")
        if "Connection refused" in str(e):
            print("  Is the container running? docker ps | grep rtorrent")
        return False


def main():
    print("Download Client Test Suite")
    print("=" * 50)
    print("Make sure containers are running:")
    print("  docker compose -f docker-compose.test-clients.yml up -d")

    results = {}

    # Test usenet clients
    print("\n" + "=" * 50)
    print("USENET CLIENTS")
    print("=" * 50)
    results["nzbget"] = test_nzbget()
    results["sabnzbd"] = test_sabnzbd()

    # Test torrent clients
    print("\n" + "=" * 50)
    print("TORRENT CLIENTS")
    print("=" * 50)
    results["qbittorrent"] = test_qbittorrent()
    results["transmission"] = test_transmission()
    results["deluge"] = test_deluge()
    results["rtorrent"] = test_rtorrent()

    # Summary
    print("\n" + "=" * 50)
    print("SUMMARY")
    print("=" * 50)

    for client, success in results.items():
        status = "PASS" if success else "FAIL"
        print(f"  {client}: {status}")

    passed = sum(results.values())
    total = len(results)
    print(f"\n  Total: {passed}/{total} passed")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
