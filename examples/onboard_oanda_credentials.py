"""Check OANDA practice credentials and print the next safe startup command."""

from __future__ import annotations

import argparse
import shlex

from smc_ta import OandaCredentialOnboardingConfig, check_oanda_credential_onboarding


def main() -> int:
    parser = argparse.ArgumentParser(description="Onboard OANDA practice credentials without printing raw secrets")
    parser.add_argument("--env-file", help="Optional .env file such as .env.demo")
    parser.add_argument("--json-file", help="Optional flat JSON secret file")
    parser.add_argument("--command", help="External command that prints JSON or .env style secrets")
    parser.add_argument("--command-format", choices=("json", "env"), default="json")
    parser.add_argument("--output", default="reports/startup/oanda_credentials.json")
    parser.add_argument("--symbol", default="EURUSD")
    parser.add_argument("--timeframe", default="M15")
    parser.add_argument("--startup-output-dir", default="reports/practice_startup/oanda_latest")
    parser.add_argument("--max-spread-pips", type=float, default=2.0)
    args = parser.parse_args()

    command = tuple(shlex.split(args.command)) if args.command else ()
    result = check_oanda_credential_onboarding(
        OandaCredentialOnboardingConfig(
            env_file=args.env_file,
            json_file=args.json_file,
            command=command,
            command_format=args.command_format,
            output_report=args.output,
            symbol=args.symbol,
            timeframe=args.timeframe,
            startup_output_dir=args.startup_output_dir,
            max_spread_pips=args.max_spread_pips,
        )
    )

    print(result.summary())
    print(f"accepted_keys={','.join(result.accepted_keys) or 'none'}")
    if result.ok:
        print(f"next_command={result.startup_command()}")
    else:
        print(f"blocking_reasons={','.join(result.secret_report.blocking_reasons)}")
        if result.missing_keys:
            print(f"missing_keys={','.join(result.missing_keys)}")
        print("accepted_export_style:")
        for line in result.export_templates(prefixed=True):
            print(line)
    if result.output_report is not None:
        print(f"redacted_report={result.output_report}")
    return 0 if result.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
