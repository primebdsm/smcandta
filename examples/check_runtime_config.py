"""Validate runtime configuration before starting a bot."""

from __future__ import annotations

import argparse
import json

from smc_ta.config import RuntimeConfig


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", help="Optional .env-style config file")
    parser.add_argument("--json", help="Optional JSON config file")
    args = parser.parse_args()

    if args.env_file and args.json:
        raise SystemExit("Use either --env-file or --json, not both.")

    if args.env_file:
        config = RuntimeConfig.from_env_file(args.env_file)
    elif args.json:
        config = RuntimeConfig.from_json(args.json)
    else:
        config = RuntimeConfig.from_env()

    report = config.validate()
    print(json.dumps(config.to_safe_dict(), indent=2, default=str))
    print(report.summary())
    if not report.to_frame().empty:
        print(report.to_frame().to_string(index=False))
    raise SystemExit(0 if report.ok else 2)


if __name__ == "__main__":
    main()
