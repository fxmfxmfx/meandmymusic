"""Early diagnostics for homelabs whose host policy blocks the asyncio wakeup socket."""
import socket
import sys

import uvicorn


def main():
    try:
        left, right = socket.socketpair()
        left.close()
        right.close()
    except PermissionError:
        print("STARTUP ERROR: the host denied socketpair(AF_UNIX). This occurs before opening music folders. Check host AppArmor audit logs. For the documented Proxmox workaround, use compose.proxmox.yaml; it relaxes AppArmor only for manager and slskd. See README troubleshooting.", file=sys.stderr)
        return 1
    uvicorn.run("app.main:create_app", factory=True, host="0.0.0.0", port=8080, workers=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
