"""Check deployment secrets without printing raw secret values."""

from __future__ import annotations

import argparse
import shlex

from smc_ta import (
    CommandSecretSource,
    EnvFileSecretSource,
    EnvSecretSource,
    JsonSecretSource,
    SecretResolutionConfig,
    resolve_runtime_secrets,
    write_secret_resolution_report,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file")
    parser.add_argument("--json-file")
    parser.add_argument("--command", help="External command that prints JSON or .env style secrets")
    parser.add_argument("--command-format", choices=("json", "env"), default="json")
    parser.add_argument("--required", default="OANDA_ACCOUNT_ID,OANDA_TOKEN")
    parser.add_argument("--output")
    args = parser.parse_args()

    required = tuple(item.strip() for item in args.required.split(",") if item.strip())
    sources = [EnvSecretSource(keys=required)]
    if args.env_file:
        sources.append(EnvFileSecretSource(args.env_file))
    if args.json_file:
        sources.append(JsonSecretSource(args.json_file))
    if args.command:
        sources.append(CommandSecretSource(tuple(shlex.split(args.command)), output_format=args.command_format))

    report = resolve_runtime_secrets(
        SecretResolutionConfig(
            sources=tuple(sources),
            required_keys=required,
        )
    )

    print(report.summary())
    print(report.safe_values())
    frame = report.to_frame()
    if not frame.empty:
        print(frame.to_string(index=False))
    if args.output:
        output = write_secret_resolution_report(report, args.output)
        print(f"secret_report_written={output}")
    raise SystemExit(0 if report.ok else 2)


if __name__ == "__main__":
    main()
