"""Home VPN: parse and validate a standard WireGuard config file (the kind wg-quick, wg-easy,
home routers and VPN providers hand out), so it can be written to the router's own config.

Supports one [Interface] and one [Peer]. The private key never leaves the admin service and
the router; nothing here returns it to the browser.
"""
import base64
import ipaddress
import re

from router import RouterError

HOST_RE = re.compile(r"^(?=.{1,253}$)([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*\.?$")


def check_wg_key(value, what):
    value = (value or "").strip()
    try:
        raw = base64.b64decode(value, validate=True)
    except Exception:
        raw = b""
    if len(raw) != 32:
        raise RouterError(f"{what} doesn't look like a WireGuard key (44 characters ending in '=').")
    return value


def _split(value):
    return [v.strip() for v in value.split(",") if v.strip()]


def parse_endpoint(value):
    value = (value or "").strip()
    m = re.fullmatch(r"\[([0-9A-Fa-f:.]+)\]:(\d+)", value) or re.fullmatch(r"([^:\s]+):(\d+)", value)
    if not m:
        raise RouterError("Endpoint must look like vpn.example.com:51820.")
    host, port = m.group(1), int(m.group(2))
    if not 1 <= port <= 65535:
        raise RouterError("Endpoint port must be between 1 and 65535.")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if not HOST_RE.match(host):
            raise RouterError("Endpoint host isn't a valid name or IP address.") from None
    return host, port


def parse_wg_conf(text):
    """Return a dict with the settings, or raise RouterError with a message for people."""
    if not isinstance(text, str) or len(text) > 8000:
        raise RouterError("Paste a WireGuard config file (it's a few lines of text).")
    sections, cur = [], None
    for n, line in enumerate(text.splitlines(), 1):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            cur = {"_name": line[1:-1].strip().lower()}
            sections.append(cur)
            continue
        if "=" not in line or cur is None:
            raise RouterError(f"Line {n} of the config isn't 'Key = value' inside a [section].")
        k, v = line.split("=", 1)
        k = k.strip().lower()
        cur[k] = f"{cur[k]}, {v.strip()}" if k in cur and k in ("address", "dns", "allowedips") else v.strip()
    ifaces = [s for s in sections if s["_name"] == "interface"]
    peers = [s for s in sections if s["_name"] == "peer"]
    if len(ifaces) != 1 or len(peers) != 1:
        raise RouterError("The config needs exactly one [Interface] and one [Peer] section.")
    i, p = ifaces[0], peers[0]

    addresses = []
    for a in _split(i.get("address", "")):
        try:
            addresses.append(str(ipaddress.ip_interface(a)))
        except ValueError:
            raise RouterError(f"Address {a!r} isn't a valid IP address.") from None
    if not addresses:
        raise RouterError("The [Interface] section needs an Address.")

    dns = []
    for d in _split(i.get("dns", "")):
        try:
            dns.append(str(ipaddress.ip_address(d)))
        except ValueError:
            pass  # wg-quick also allows search domains here; OpenWrt doesn't need them

    mtu = i.get("mtu")
    if mtu is not None:
        if not mtu.isdigit() or not 1280 <= int(mtu) <= 1500:
            raise RouterError("MTU must be a number between 1280 and 1500.")

    allowed = []
    for a in _split(p.get("allowedips", "0.0.0.0/0, ::/0")):
        try:
            allowed.append(str(ipaddress.ip_network(a, strict=False)))
        except ValueError:
            raise RouterError(f"AllowedIPs entry {a!r} isn't a valid network.") from None

    keepalive = p.get("persistentkeepalive", "25")
    if not keepalive.isdigit() or int(keepalive) > 65535:
        raise RouterError("PersistentKeepalive must be a number of seconds.")

    host, port = parse_endpoint(p.get("endpoint"))
    psk = p.get("presharedkey")
    return {
        "private_key": check_wg_key(i.get("privatekey"), "PrivateKey"),
        "addresses": addresses,
        "dns": dns,
        "mtu": mtu,
        "public_key": check_wg_key(p.get("publickey"), "The peer's PublicKey"),
        "preshared_key": check_wg_key(psk, "PresharedKey") if psk else None,
        "endpoint_host": host,
        "endpoint_port": port,
        "allowed_ips": allowed,
        "keepalive": keepalive,
    }


def full_tunnel(allowed_ips):
    """True when the config sends all internet traffic through the tunnel."""
    return any(a in ("0.0.0.0/0", "::/0") for a in allowed_ips)
