"""Validate real Telegram, Discord, and email alert delivery."""

from __future__ import annotations

import argparse

from smc_ta import AlertChannelValidationConfig, parse_alert_channel_names, validate_alert_channels


def main() -> int:
    parser = argparse.ArgumentParser(description="Send explicit redaction-safe probes to configured alert channels")
    parser.add_argument("--env-file")
    parser.add_argument("--channels", default="telegram,discord,email")
    parser.add_argument("--output", default="reports/startup/alert_validation.json")
    parser.add_argument("--message", default="SMC TA real alert channel validation probe")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--include-memory", action="store_true")
    parser.add_argument("--allow-unconfigured", action="store_true")
    parser.add_argument("--warning-on-failure", action="store_true")
    args = parser.parse_args()

    result = validate_alert_channels(
        AlertChannelValidationConfig(
            env_file=args.env_file,
            channel_names=parse_alert_channel_names(args.channels),
            probe_message=args.message,
            include_memory=args.include_memory,
            require_configured=not args.allow_unconfigured,
            blocking_on_failure=not args.warning_on_failure,
            timeout=args.timeout,
            output_report=args.output,
        )
    )

    print(result.summary())
    print(f"configured_channels={','.join(result.build.configured_channels) or 'none'}")
    if result.build.partial_channels:
        print(f"partial_channels={','.join(result.build.partial_channels)}")
    if result.build.missing_channels:
        print(f"missing_channels={','.join(result.build.missing_channels)}")
    for status in result.statuses:
        print(f"{status.channel_name}={status.status}:{status.message}")
    if result.output_report is not None:
        print(f"report={result.output_report}")
    return 0 if result.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
