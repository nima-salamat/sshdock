from __future__ import annotations

import argparse
import ipaddress
import os
import socket
import sys
import threading


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    parts: list[bytes] = []
    remaining = size
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise OSError("Proxy closed the connection during handshake")
        parts.append(chunk)
        remaining -= len(chunk)
    return b"".join(parts)


def _socks5_connect(sock: socket.socket, host: str, port: int) -> None:
    sock.sendall(b"\x05\x01\x00")
    greeting = _recv_exact(sock, 2)
    if greeting != b"\x05\x00":
        if greeting == b"\x05\xff":
            raise OSError("SOCKS5 proxy rejected the no-auth method")
        raise OSError("Invalid SOCKS5 proxy handshake")

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        encoded = host.encode("idna")
        if len(encoded) > 255:
            raise ValueError("Target hostname is too long for SOCKS5")
        request = b"\x05\x01\x00\x03" + bytes([len(encoded)]) + encoded
    else:
        if address.version == 4:
            request = b"\x05\x01\x00\x01" + address.packed
        else:
            request = b"\x05\x01\x00\x04" + address.packed

    request += int(port).to_bytes(2, "big")
    sock.sendall(request)

    response = _recv_exact(sock, 4)
    if response[0] != 5:
        raise OSError("Invalid SOCKS5 proxy response")
    if response[1] != 0:
        reasons = {
            1: "general SOCKS5 failure",
            2: "connection not allowed",
            3: "network unreachable",
            4: "host unreachable",
            5: "connection refused",
            6: "TTL expired",
            7: "command not supported",
            8: "address type not supported",
        }
        raise OSError(reasons.get(response[1], f"SOCKS5 proxy error {response[1]}"))

    atyp = response[3]
    if atyp == 1:
        _recv_exact(sock, 4)
    elif atyp == 3:
        length = _recv_exact(sock, 1)[0]
        _recv_exact(sock, length)
    elif atyp == 4:
        _recv_exact(sock, 16)
    else:
        raise OSError("Invalid SOCKS5 bound-address type")
    _recv_exact(sock, 2)


def _http_connect(sock: socket.socket, host: str, port: int) -> None:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        authority = host
    else:
        authority = f"[{host}]" if ":" in host else host
    authority = f"{authority}:{int(port)}"
    request = (
        f"CONNECT {authority} HTTP/1.1\r\n"
        f"Host: {authority}\r\n"
        "Proxy-Connection: Keep-Alive\r\n"
        "\r\n"
    ).encode("ascii", "strict")
    sock.sendall(request)

    response = bytearray()
    while b"\r\n\r\n" not in response:
        chunk = sock.recv(4096)
        if not chunk:
            raise OSError("HTTP proxy closed the connection during CONNECT")
        response.extend(chunk)
        if len(response) > 64 * 1024:
            raise OSError("HTTP proxy response headers are too large")

    first_line = bytes(response).split(b"\r\n", 1)[0].decode("iso-8859-1", "replace")
    parts = first_line.split(None, 2)
    if len(parts) < 2 or not parts[0].startswith("HTTP/"):
        raise OSError(f"Invalid HTTP proxy response: {first_line!r}")
    try:
        status = int(parts[1])
    except ValueError as exc:
        raise OSError(f"Invalid HTTP proxy status: {first_line!r}") from exc
    if status < 200 or status >= 300:
        reason = parts[2] if len(parts) > 2 else ""
        raise OSError(f"HTTP proxy CONNECT failed: {status} {reason}".strip())


def _relay(sock: socket.socket) -> int:
    stop = threading.Event()

    def stdin_to_socket() -> None:
        try:
            while not stop.is_set():
                chunk = os.read(sys.stdin.fileno(), 64 * 1024)
                if not chunk:
                    break
                sock.sendall(chunk)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            stop.set()
            try:
                sock.shutdown(socket.SHUT_WR)
            except OSError:
                pass

    thread = threading.Thread(target=stdin_to_socket, name="proxy-stdin", daemon=True)
    thread.start()

    try:
        while not stop.is_set():
            chunk = sock.recv(64 * 1024)
            if not chunk:
                break
            os.write(sys.stdout.fileno(), chunk)
    except (BrokenPipeError, ConnectionResetError, OSError):
        pass
    finally:
        stop.set()
        thread.join(timeout=0.5)
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SSHDock local HTTP/SOCKS5 proxy bridge")
    parser.add_argument("--type", required=True, choices=("socks5", "http_connect"))
    parser.add_argument("--proxy-host", required=True)
    parser.add_argument("--proxy-port", required=True, type=int)
    parser.add_argument("--target-host", required=True)
    parser.add_argument("--target-port", required=True, type=int)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if not (1 <= args.proxy_port <= 65535 and 1 <= args.target_port <= 65535):
        print("Proxy and target ports must be between 1 and 65535", file=sys.stderr)
        return 2

    if os.name == "nt":
        import msvcrt
        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)

    sock: socket.socket | None = None
    try:
        sock = socket.create_connection((args.proxy_host, args.proxy_port), timeout=15)
        sock.settimeout(15)
        if args.type == "socks5":
            _socks5_connect(sock, args.target_host, args.target_port)
        else:
            _http_connect(sock, args.target_host, args.target_port)
        sock.settimeout(None)
        return _relay(sock)
    except Exception as exc:
        print(f"Proxy connection failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
