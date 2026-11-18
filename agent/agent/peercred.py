"""Who is on the other end of a unix socket, as the kernel reports it."""

import socket
import struct
import sys
from contextvars import ContextVar
from dataclasses import dataclass

# struct ucred (Linux): pid, uid, gid as three ints.
_UCRED = struct.Struct("3i")
# struct xucred (macOS): cr_version (u_int), cr_uid (uid_t), cr_ngroups (short), cr_groups[16].
_XUCRED = struct.Struct("IIh16I")
_SOL_LOCAL = 0


@dataclass(frozen=True)
class Peer:
    uid: int
    gid: int


# Set by the server around each handler call, so a handler can name its caller.
current_peer: ContextVar[Peer | None] = ContextVar("current_peer", default=None)


def peer_credentials(sock: socket.socket) -> tuple[int, int]:
    """(uid, gid) of the connected peer. Raises OSError when the platform cannot tell."""
    if sys.platform.startswith("linux"):
        raw = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, _UCRED.size)
        _pid, uid, gid = _UCRED.unpack(raw)
        return uid, gid
    if sys.platform == "darwin":
        raw = sock.getsockopt(_SOL_LOCAL, socket.LOCAL_PEERCRED, _XUCRED.size)
        _version, uid, ngroups, *groups = _XUCRED.unpack(raw)
        if ngroups < 1:
            raise OSError("peer has no group")
        return uid, groups[0]
    raise OSError(f"peer credentials are not supported on {sys.platform}")
