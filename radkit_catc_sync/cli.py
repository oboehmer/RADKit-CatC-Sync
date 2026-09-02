"""Command-line interface for radkit-catc-sync."""

from __future__ import annotations

import argparse
import dataclasses
import logging
import sys
from pathlib import Path

from .config import load_config, load_env_file, load_env_vars
from .sync import CatCInventoryError, run_sync

logger = logging.getLogger(__name__)


def setup_logging(verbose: bool) -> None:
    """Configure logging for the application."""
    level = logging.DEBUG if verbose else logging.INFO
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(message)s", "%H:%M:%S"))

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.addHandler(handler)

    # Suppress urllib3 SSL warnings for self-signed certs
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def main() -> int:
    """
    Main entry point for radkit-catc-sync CLI.

    Returns:
        Exit code (0 for success, 1 for errors).
    """
    parser = argparse.ArgumentParser(
        description="Sync Cisco Catalyst Center device inventory into a RADKit service.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Environment variables (can also be set in a .env file):\n"
            "  CATC_USER              Catalyst Center username\n"
            "  CATC_PASSWORD          Catalyst Center password\n"
            "  RADKIT_ADMIN_USER      RADKit ControlAPI admin username\n"
            "  RADKIT_ADMIN_PASSWORD  RADKit ControlAPI admin password\n"
            "  RADKIT_SSH_USER        SSH username for imported devices\n"
            "  RADKIT_SSH_PASSWORD    SSH password for imported devices\n"
            "\n"
            "Config file (TOML format):\n"
            "  Specify with -c/--config or CATC_SYNC_CONFIG env var, "
            "or place catc_sync.toml in cwd.\n"
        ),
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=None,
        metavar="FILE",
        help="Path to TOML config file (default: CATC_SYNC_CONFIG env var, then ./catc_sync.toml).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be changed without making any modifications.",
    )
    parser.add_argument(
        "--update-passwords",
        action="store_true",
        help="Overwrite SSH password on existing managed devices.",
    )
    parser.add_argument(
        "-A",
        "--adopt-existing",
        action="store_true",
        default=None,
        help=(
            "Adopt unmanaged RADKit devices that share a name with a CatC device. "
            "Without this flag such conflicts are skipped with a warning. "
            "(Can also be set via sync.adopt_existing in config file.)"
        ),
    )
    parser.add_argument(
        "-k",
        "--no-verify-tls",
        action="store_true",
        default=None,
        help=(
            "Disable TLS certificate verification for Catalyst Center connections. "
            "(Can also be set via catc.verify_tls in config file.)"
        ),
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug-level logging.",
    )
    args = parser.parse_args()

    setup_logging(args.verbose)

    # Load configuration from TOML file
    try:
        config = load_config(args.config)
    except FileNotFoundError as e:
        logger.error("%s", e)
        return 1

    # Load .env from current directory
    load_env_file([Path.cwd()])

    # Apply CLI flag overrides to config (CLI wins when explicitly set)
    if args.no_verify_tls is not None:
        config = dataclasses.replace(config, catc_verify_tls=not args.no_verify_tls)
    if args.adopt_existing is not None:
        config = dataclasses.replace(config, adopt_existing=args.adopt_existing)

    if args.dry_run:
        logger.info("*** DRY-RUN MODE — no changes will be made ***")

    # Load and validate environment variables
    try:
        env = load_env_vars(config)
    except SystemExit as e:
        logger.error("%s", e)
        return 1

    # Run sync
    try:
        stats = run_sync(
            config=config,
            dry_run=args.dry_run,
            update_passwords=args.update_passwords,
            catc_user=env["CATC_USER"],
            catc_password=env["CATC_PASSWORD"],
            radkit_admin_user=env["RADKIT_ADMIN_USER"],
            radkit_admin_password=env["RADKIT_ADMIN_PASSWORD"],
            ssh_user=env["RADKIT_SSH_USER"],
            ssh_password=env["RADKIT_SSH_PASSWORD"],
        )
    except CatCInventoryError as exc:
        logger.error("Aborting sync: %s", exc)
        logger.error("No changes were made. Fix CatC connectivity/credentials and retry.")
        return 1
    except ValueError as exc:
        logger.error("%s", exc)
        return 1

    # Print summary
    print()
    print(stats.summary(dry_run=args.dry_run))

    return 1 if stats.errors else 0


if __name__ == "__main__":
    sys.exit(main())
