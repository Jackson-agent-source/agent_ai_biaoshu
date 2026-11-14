"""Configure an existing deployment for a public IPv4 address, preserving secrets."""

import argparse
from datetime import datetime, timezone
import ipaddress
import os
from pathlib import Path
import re
import tempfile


def configure(env_path: Path, address: str):
    ip = ipaddress.IPv4Address(address)
    if not ip.is_global or ip.is_multicast or ip.is_reserved:
        raise ValueError("Use the server's public IPv4 address.")
    if env_path.is_symlink():
        raise ValueError("Refusing to replace a symbolic-link .env file.")
    original_bytes = env_path.read_bytes()
    original = original_bytes.decode("utf-8-sig")
    origin = f"https://{ip}"
    updates = {
        "PUBLIC_IP": str(ip),
        "SITE_ORIGIN": origin,
        "ALLOWED_HOSTS": f"{ip},127.0.0.1,localhost",
        "CSRF_TRUSTED_ORIGINS": origin,
        "CORS_ALLOWED_ORIGINS": "",
        "MEDIA_PUBLIC_BASE_URL": origin,
        "DEBUG": "False",
        "USE_HTTPS": "True",
        "WEB_BIND": "127.0.0.1",
        "CADDY_IMAGE": "caddy:2.11.9-alpine",
        "CADDY_CONFIG_FILE": "./deploy/Caddyfile.ip",
    }
    lines, written = [], set()
    for line in original.splitlines():
        match = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        key = match.group(1) if match else None
        if key in updates:
            if key not in written:
                lines.append(f"{key}={updates[key]}")
                written.add(key)
        else:
            lines.append(line)
    lines.extend(f"{key}={value}" for key, value in updates.items() if key not in written)
    updated = "\n".join(lines) + "\n"
    if updated.encode("utf-8") == original_bytes:
        return None

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = env_path.with_name(f".env.backup-ip-{stamp}")
    fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(original_bytes)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=env_path.parent, prefix=".env.ip-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(updated)
        temporary.chmod(0o600)
        os.replace(temporary, env_path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return backup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ip", help="Public IPv4 of this server")
    args = parser.parse_args()
    env_path = Path(__file__).resolve().parents[1] / ".env"
    try:
        backup = configure(env_path, args.ip)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Configuration not applied: {exc}\n")
    print(f"Environment backup: {backup.name}" if backup else "IP configuration already applied.")
    print(f"Site address: https://{args.ip}")
    print("Database credentials, API keys, registration policy and data volumes are unchanged.")


if __name__ == "__main__":
    main()
