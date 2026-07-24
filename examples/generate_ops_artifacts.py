"""Generate supervisor and logrotate artifacts for a bot process."""

from __future__ import annotations

import argparse
import shlex

from smc_ta import LogrotateConfig, SupervisorConfig, write_supervisor_artifacts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="deployment/supervisor")
    parser.add_argument("--service-name", default="smc-ta-demo")
    parser.add_argument("--description", default="SMC TA Forex demo bot")
    parser.add_argument("--command", default="python examples/demo_paper_loop.py")
    parser.add_argument("--working-directory", default=".")
    parser.add_argument("--env-file")
    parser.add_argument("--log-dir", default="logs")
    parser.add_argument("--user")
    parser.add_argument("--rotate-count", type=int, default=14)
    args = parser.parse_args()

    config = SupervisorConfig(
        service_name=args.service_name,
        description=args.description,
        command=tuple(shlex.split(args.command)),
        working_directory=args.working_directory,
        env_file=args.env_file,
        log_dir=args.log_dir,
        user=args.user,
    )
    bundle = write_supervisor_artifacts(
        config,
        args.output_dir,
        logrotate=LogrotateConfig(
            name=args.service_name,
            log_glob=f"{args.log_dir}/*.log",
            rotate_count=args.rotate_count,
        ),
    )

    print(f"ops_artifacts_written={bundle.output_dir}")
    print(f"systemd_unit={bundle.systemd_unit}")
    print(f"launchd_plist={bundle.launchd_plist}")
    print(f"logrotate_config={bundle.logrotate_config}")


if __name__ == "__main__":
    main()
