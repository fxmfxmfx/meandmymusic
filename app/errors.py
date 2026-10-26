from fastapi import HTTPException


class ConnectionFailure(HTTPException):
    """Stable, safe diagnostics. Never return raw upstream logs or exception text."""
    def __init__(self, code, message, *, stage="soulseek", hint="", status=502):
        super().__init__(status_code=status, detail=message)
        self.info = {"code": code, "stage": stage, "message": message, "hint": hint}


def classify_connection_log(message):
    value = message.lower()
    if "invalidpass" in value or "invalid password" in value or "incorrect password" in value:
        return ConnectionFailure("INVALID_PASSWORD", "Soulseek rejected the password, or the username is already taken.", stage="login", hint="Check the password for an existing account, or choose a different name for a new account.", status=400)
    if "invalidusername" in value or "invalid username" in value:
        return ConnectionFailure("INVALID_USERNAME", "Soulseek rejected the username.", stage="login", hint="Check the username or choose another one.", status=400)
    if "logged in elsewhere" in value or "loggedinelsewhere" in value or "another location" in value:
        return ConnectionFailure("LOGGED_IN_ELSEWHERE", "This account signed in from another client.", stage="login", hint="Disconnect the account in other clients and try again.", status=409)
    if "banned" in value or "ip blocked" in value:
        return ConnectionFailure("SERVER_REJECTED", "The server reported that the connection was blocked.", stage="login", hint="Check the network rules and connection address.", status=403)
    if "socks" in value or "proxy" in value:
        if any(word in value for word in ["auth", "credential", "username", "password"]):
            return ConnectionFailure("PROXY_AUTH_FAILED", "Proxy authentication failed.", stage="proxy", hint="Check the SOCKS5 credentials in OUTBOUND_PROXY.")
        return ConnectionFailure("PROXY_CONNECTION_FAILED", "slskd could not connect through the SOCKS5 proxy.", stage="proxy", hint="Check the proxy address, port and SOCKS5 CONNECT support.")
    if any(word in value for word in ["no such host", "name or service not known", "name resolution", "host is unknown", "nodename nor servname"]):
        return ConnectionFailure("DNS_FAILED", "Could not resolve the server address.", stage="network", hint="Check DNS on the host and remote DNS support on the proxy.")
    if "refused" in value:
        return ConnectionFailure("CONNECTION_REFUSED", "The server refused the connection.", stage="network", hint="Check outbound connectivity, firewall rules and proxy settings.")
    if "unreachable" in value or "no route to host" in value:
        return ConnectionFailure("NETWORK_UNREACHABLE", "No network route to the server.", stage="network", hint="Check routing, VPN and proxy settings.")
    if "timed out" in value or "timeout" in value:
        return ConnectionFailure("CONNECTION_TIMEOUT", "Connection to the server timed out.", stage="network", hint="Check connectivity to Soulseek from the host network.", status=504)
    return None
