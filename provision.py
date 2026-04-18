#!/usr/bin/env python3
"""
provision.py — One-Time Secret Provisioning Script

Consumes a provisioning token from the Legisell API,
copies .env.example → .env, and replaces matching placeholder values
with the actual secrets. Unmatched keys are appended at the bottom.

Usage:
    python3 provision.py --token <ONE_TIME_PROVISIONING_TOKEN> --api-url <LEGISELL_BACKEND_URL> [--env-example <PATH>] [--env-output <PATH>]

Examples:
    python3 provision.py --token abc123 --api-url https://LEGISELL_BACKEND_URL
    python3 provision.py --token abc123 --api-url https://LEGISELL_BACKEND_URL --env-example .env.example --env-output .env
"""

import argparse
import json
import re
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Consume a Legisell provisioning token and write secrets to a .env file.",
    )
    parser.add_argument(
        "--token", required=True, help="One-time provisioning token",
    )
    parser.add_argument(
        "--api-url",
        required=True,
        help="Base URL of the Legisell API (e.g. https://admin.legisell.de)",
    )
    parser.add_argument(
        "--env-example",
        default=".env.example",
        help="Path to .env.example template (default: .env.example)",
    )
    parser.add_argument(
        "--env-output",
        default=".env",
        help="Path for output .env file (default: .env)",
    )
    return parser.parse_args()


def consume_token(api_url: str, token: str) -> dict:
    """POST the token to the provisioning endpoint and return the JSON response."""
    endpoint = f"{api_url.rstrip('/')}/api/public/provision"
    payload = json.dumps({"token": token}).encode()

    req = urllib.request.Request(
        endpoint,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            err_body = json.loads(exc.read().decode())
            detail = err_body.get("detail", "")
        except Exception:
            pass
        msg = f"API request failed (HTTP {exc.code})."
        if detail:
            msg += f"\nDetail: {detail}"
        print(msg, file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as exc:
        print(f"Connection error: {exc.reason}", file=sys.stderr)
        sys.exit(1)

    return body


def write_env(
    secrets: list[dict],
    env_example: Path,
    env_output: Path,
) -> None:
    """Copy env_example → env_output, replace matching keys, append the rest."""
    # Backup existing .env if it exists
    if env_output.is_file():
        backup_path = env_output.with_stem(f"{env_output.stem}.backup")
        shutil.copy2(env_output, backup_path)
        print(f"Backed up existing {env_output} → {backup_path}")
    
    shutil.copy2(env_example, env_output)
    print(f"Copied {env_example} → {env_output}")

    content = env_output.read_text(encoding="utf-8")
    appended: list[str] = []

    for secret in secrets:
        key = secret["key_name"]
        value = secret["value"]
        # Replace existing KEY=... line (match KEY= at line start, any value after)
        new_content, count = re.subn(
            rf"^{re.escape(key)}=.*$",
            lambda _m, k=key, v=value: f"{k}={v}",
            content,
            flags=re.MULTILINE,
        )
        if count > 0:
            content = new_content
            print(f"  Replaced: {key}")
        else:
            appended.append(f"{key}={value}")

    env_output.write_text(content, encoding="utf-8")

    if appended:
        with env_output.open("a", encoding="utf-8") as f:
            f.write("\n# ── Provisioned from Legisell Secrets ──────────────────────────────────\n")
            for entry in appended:
                f.write(f"{entry}\n")
        print(f"  Appended {len(appended)} additional secret(s).")


def main() -> None:
    args = parse_args()

    env_example = Path(args.env_example)
    if not env_example.is_file():
        print(f"Error: Template file not found: {env_example}", file=sys.stderr)
        sys.exit(1)

    print(f"Fetching secrets from {args.api_url.rstrip('/')}/api/public/provision ...")

    data = consume_token(args.api_url, args.token)
    tenant = data.get("tenant", "unknown")
    secrets = data.get("secrets", [])

    print(f"Received {len(secrets)} secrets for tenant: {tenant}")

    env_output = Path(args.env_output)
    write_env(secrets, env_example, env_output)

    print(f"\nProvisioning complete. Environment file: {env_output}")


if __name__ == "__main__":
    main()
