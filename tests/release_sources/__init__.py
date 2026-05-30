"""
Stub out flask_socketio only when it is not actually installed, so the IRC
source can be imported in lightweight test environments without the real
dependency. In full dev environments the real package is used as-is.
"""

import sys
import types


def _stub_module_if_missing(name: str, attrs: dict) -> None:
    if name in sys.modules:
        return
    try:
        __import__(name)
    except ImportError:
        stub = types.ModuleType(name)
        for attr, value in attrs.items():
            setattr(stub, attr, value)
        sys.modules[name] = stub


_stub_module_if_missing(
    "flask_socketio",
    {
        "SocketIO": object,
        "emit": lambda *a, **kw: None,
        "join_room": lambda *a, **kw: None,
        "leave_room": lambda *a, **kw: None,
    },
)
