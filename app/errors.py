from fastapi import HTTPException


class ConnectionFailure(HTTPException):
    """Stable, safe diagnostics. Never return raw upstream logs or exception text."""
    def __init__(self, code, message, *, stage="soulseek", hint="", status=502):
        super().__init__(status_code=status, detail=message)
        self.info = {"code": code, "stage": stage, "message": message, "hint": hint}


def classify_connection_log(message):
    value = message.lower()
    if "invalidpass" in value or "invalid password" in value or "incorrect password" in value:
        return ConnectionFailure("INVALID_PASSWORD", "Soulseek отклонил пароль или имя уже занято.", stage="login", hint="Для существующей учётки проверь пароль. Для новой выбери другое имя.", status=400)
    if "invalidusername" in value or "invalid username" in value:
        return ConnectionFailure("INVALID_USERNAME", "Сервер Soulseek отклонил имя учётки.", stage="login", hint="Проверь имя или выбери другое.", status=400)
    if "logged in elsewhere" in value or "loggedinelsewhere" in value or "another location" in value:
        return ConnectionFailure("LOGGED_IN_ELSEWHERE", "Эта учётка вошла через другой клиент.", stage="login", hint="Отключи её в Nicotine+/SoulseekQt и повтори вход.", status=409)
    if "banned" in value or "ip blocked" in value:
        return ConnectionFailure("SERVER_REJECTED", "Сервер сообщил о блокировке подключения.", stage="login", hint="Проверь правила сети и адрес подключения. Смена пароля сама по себе это не исправляет.", status=403)
    if "socks" in value or "proxy" in value:
        if any(word in value for word in ["auth", "credential", "username", "password"]):
            return ConnectionFailure("PROXY_AUTH_FAILED", "Прокси отклонил авторизацию.", stage="proxy", hint="Проверь логин и пароль SOCKS5-прокси в OUTBOUND_PROXY.")
        return ConnectionFailure("PROXY_CONNECTION_FAILED", "slskd не смог подключиться через SOCKS5-прокси.", stage="proxy", hint="Проверь адрес, порт и поддержку SOCKS5 CONNECT у прокси.")
    if any(word in value for word in ["no such host", "name or service not known", "name resolution", "host is unknown", "nodename nor servname"]):
        return ConnectionFailure("DNS_FAILED", "Не удалось определить адрес сервера.", stage="network", hint="Проверь DNS хомлаба и доступность DNS/удалённого разрешения имён у прокси.")
    if "refused" in value:
        return ConnectionFailure("CONNECTION_REFUSED", "Соединение с сервером отклонено.", stage="network", hint="Проверь исходящие подключения к серверу Soulseek, firewall и настройки прокси.")
    if "unreachable" in value or "no route to host" in value:
        return ConnectionFailure("NETWORK_UNREACHABLE", "Нет сетевого маршрута до сервера.", stage="network", hint="Проверь маршрутизацию, VPN и настройки прокси.")
    if "timed out" in value or "timeout" in value:
        return ConnectionFailure("CONNECTION_TIMEOUT", "Истекло время подключения к серверу.", stage="network", hint="Проверь доступность Soulseek из сети сервера; при необходимости настрой SOCKS5-прокси.", status=504)
    return None
