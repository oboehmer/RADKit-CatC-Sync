# RADKit-CatC-Sync

Synchronise Cisco Catalyst Center (formerly DNA Center) device inventory into a [RADKit](https://radkit.cisco.com) service using the RADKit ControlAPI.

## Features

- Imports managed network devices from one or more Catalyst Center clusters
- Creates, updates, and deletes devices in RADKit inventory to stay in sync
- Tracks ownership via `catc_source` metadata — only manages what it imports
- Deletion is scoped: only removes devices from clusters that were synced this run
- Whitelist/blacklist regex filters on device names
- Reports how many devices were fetched per cluster and why devices were ignored (blacklist, whitelist miss, no hostname, collisions, unmanaged)
- Fetches CatC clusters and RADKit inventory in parallel, and applies add/update/delete changes via batched (bulk) ControlAPI calls for faster syncs
- Narrowing filters removes previously-synced devices from RADKit (same as devices removed from CatC)
- Cross-cluster hostname collision detection (first cluster wins, warning logged)
- `--dry-run` mode to preview all changes before applying them
- `--adopt-existing` to take ownership of manually-added RADKit devices
- `--update-credentials` to refresh SSH credentials on existing devices in RADKit inventory
- Loads `.env` and `catc_sync.toml` from the current working directory

## Comparison with RADKit's built-in sync

RADKit-Service can also sync CatC inventory into RADKit (and other Cisco
controllers), but this package offers more flexibility, namely:

- filter devices by name (RADKit-Service currently only supports filtering by CatC device tags)
- use different device credentials than CatC
- control the metadata items which are added to RADKit inventory

If you don't need any of these capabilities, it's best to stick with
RADKit-Service's built-in sync mechanism.

## Requirements

- Python 3.11+
- cisco-radkit-service packages installed
- RADKit packages are distributed via Cisco's private PyPI index: `https://radkit.cisco.com/pip`

## Installation

### With uv (Recommended)

[uv](https://docs.astral.sh/uv/) is the fastest way to install, with automatic PyPI index handling:

```bash
uv tool install --extra-index-url=https://radkit.cisco.com/pip git+https://github.com/oboehmer/RADKit-CatC-Sync.git
```

This installs the latest version directly from GitHub into an isolated environment and makes the `catc-sync` command available in your PATH.

To use a specific branch:

```bash
uv tool install --extra-index-url=https://radkit.cisco.com/pip git+https://github.com/oboehmer/RADKit-CatC-Sync.git@branch-name
```

### From a local clone

```bash
git clone https://github.com/oboehmer/RADKit-CatC-Sync.git
cd RADKit-CatC-Sync
uv pip install -e .
```

### With pip

pip does not read `[tool.uv]`, so the RADKit index must be passed explicitly:

```bash
pip install --extra-index-url https://radkit.cisco.com/pip \
    git+https://github.com/oboehmer/RADKit-CatC-Sync.git
```

Or from a local clone:

```bash
git clone https://github.com/oboehmer/RADKit-CatC-Sync.git
cd RADKit-CatC-Sync
pip install --extra-index-url=https://radkit.cisco.com/pip -e .
```

## Configuration

### 1. Generate a config template

The easiest way to get started is to use the `--init` flag, which generates a template in your current directory:

```bash
catc-sync --init
```

This creates `catc_sync.toml` with all available options documented. Edit it for your environment and you're ready to go.

### 2. Edit the config file manually

If you prefer to create the config file yourself:

```toml
[catc]
clusters = [
    "https://catc1.example.com",
    "https://catc2.example.com",
]

[radkit]
# Optional: override default RADKit ControlAPI URL
# base_url = "https://localhost:8081/api/v1"

[filters]
# Regex patterns applied to raw CatC hostnames (optional)
whitelist = []              # e.g. ["^router-", "^sw-"]
blacklist = []              # e.g. ["\\.lab\\.", "^test"]

[metadata]
# Optional: customize metadata handling
# source_key = "catc_source"    # ownership marker in device metadata
# fields = ["hostname", "serialNumber"]  # CatC fields to sync as metadata
```

See `catc_sync.toml.example` for complete documentation and defaults. You can also download it from GitHub if needed:

```bash
curl -o catc_sync.toml.example \
  https://raw.githubusercontent.com/oboehmer/RADKit-CatC-Sync/main/catc_sync.toml.example
```

**Config file search order** (first found is used):
1. Explicit path via `-c` / `--config` flag
2. `CATC_SYNC_CONFIG` environment variable
3. `./catc_sync.toml` (current working directory)
4. Built-in defaults if no file found

### 3. Set passwords via environment variables

Passwords **must** be provided via environment variables (never in config files). The script can load them from a `.env` file in the current working directory:

```dotenv
CATC_PASSWORD=your_catc_password
RADKIT_ADMIN_PASSWORD=your_radkit_admin_password
RADKIT_SSH_PASSWORD=your_ssh_password
```

| Variable | Description | Required |
|---|---|---|
| `CATC_USER` | Catalyst Center username | If not in `catc_sync.toml` |
| `CATC_PASSWORD` | Catalyst Center password | Always (env only) |
| `RADKIT_ADMIN_USER` | RADKit ControlAPI admin username | If not in `catc_sync.toml` |
| `RADKIT_ADMIN_PASSWORD` | RADKit ControlAPI admin password | Always (env only) |
| `RADKIT_SSH_USER` | SSH username for imported devices | If not in `catc_sync.toml` |
| `RADKIT_SSH_PASSWORD` | SSH password for imported devices | Always (env only) |

**Precedence:**
1. Environment variables (from `os.environ` or `.env` file)
2. Non-sensitive values from `catc_sync.toml` (usernames only)
3. Built-in defaults

> **Note:** `RADKIT_SSH_USER` / `radkit.ssh_user` is only applied to a device when
> it is first created in the RADKit inventory. Changing it does not touch devices
> that already exist — run `catc-sync --update-credentials` once afterwards to push
> the new username (together with the current password) onto existing managed devices.

## Usage

```bash
catc-sync [OPTIONS]
```

### Options

| Flag | Short | Description |
|---|---|---|
| `--init` | | Generate a catc_sync.toml template in the current directory |
| `--config FILE` | `-c` | Path to TOML config file |
| `--dry-run` | | Preview all changes without applying them |
| `--update-credentials` | | Overwrite SSH username and password on existing managed devices |
| `--adopt-existing` | `-A` | Take ownership of unmanaged RADKit devices matching CatC names |
| `--no-verify-tls` | `-k` | Disable TLS certificate verification for Catalyst Center |
| `--verbose` | `-v` | Enable debug-level logging |
| `--help` | `-h` | Show help message |

### Examples

```bash
# Generate a config template
catc-sync --init

# Preview what would happen (safe, read-only)
catc-sync --dry-run

# Normal sync
catc-sync

# Sync and refresh SSH credentials (username + password) on existing devices
catc-sync --update-credentials

# Take over manually-added devices that now exist in CatC
catc-sync --adopt-existing --dry-run    # preview first
catc-sync --adopt-existing              # apply

# Use a specific config file
catc-sync -c /etc/radkit/catc_sync.toml

# Debug mode with verbose logging
catc-sync --verbose --dry-run
```

### Example output

```
12:34:01 INFO     Connecting to RADKit ControlAPI at https://localhost:8081/api/v1
12:34:01 INFO     Fetching existing RADKit devices and inventory from 2 CatC cluster(s) in parallel...
12:34:03 INFO     Fetched 310 devices from CatC catc1.example.com
12:34:03 INFO     Fetched 280 devices from CatC catc2.example.com
12:34:03 INFO     Found 42 catc-managed and 3 unmanaged devices in RADKit
12:34:03 INFO     Total importable devices across all clusters: 585
12:34:03 INFO     Devices to add (or adopt): 543
12:34:03 INFO     Devices to check for updates: 42
12:34:03 INFO     Devices to delete: 0

--- Sync Summary ---
  Fetched:         590  (across 2 cluster(s))
    catc1.example.com:  310
    catc2.example.com:  280
  Added:           543
  Updated:         42
  Unchanged:       0
  Adopted:         0  (existing unmanaged devices taken over)
  Deleted:         0
  Skipped:         5
    blacklisted: 3
    not whitelisted: 1
    hostname collision: 1
  Errors:          0
```

## How ownership tracking works

Every device imported by this script has a `catc_source` metadata field set to the Catalyst Center hostname (e.g., `catc1.example.com`). This field serves as the ownership marker:

- **Non-empty `catc_source`** → managed by this script
- **Missing or empty `catc_source`** → manually added, not modified by default

On each run, the script:

1. Fetches all RADKit devices and splits them into managed (with `catc_source`) / unmanaged sets
2. Fetches fresh inventory from all configured Catalyst Center clusters
3. **Adds** devices new to RADKit
4. **Updates** existing devices if IP, type, or metadata changed
5. **Deletes** devices no longer in CatC (or excluded by filters)
6. **Deletion scope** — only removes a device if its `catc_source` matches a cluster synced this run

### Example: filter changes

If you narrow your whitelist (e.g., to exclude lab devices), devices that no longer match are deleted from RADKit on the next sync. This is intentional — filters act as a device definition. Use `--dry-run` to preview before applying changes.

## Development

### Setup

```bash
# Clone repository
git clone https://github.com/oboehmer/RADKit-CatC-Sync.git
cd RADKit-CatC-Sync

# Install with dev dependencies (using uv)
uv pip install -e ".[dev]"

# Or with pip
pip install --extra-index-url https://radkit.cisco.com/pip -e ".[dev]"
```

### Testing & linting

```bash
# Run tests
pytest

# Type check
mypy radkit_catc_sync tests/

# Lint and format
ruff check radkit_catc_sync tests/
ruff format radkit_catc_sync tests/
```

### Pre-commit hooks

This repo ships a [`.pre-commit-config.yaml`](.pre-commit-config.yaml) that runs
`ruff` (lint + format) and `mypy` automatically before each commit.

The `mypy` hook uses `language: system` — it runs the `mypy` from your **active
virtualenv**, so RADKit's types are checked against the real installed packages.
Install the dev dependencies (which pull in RADKit) into that env first, and run
`pre-commit` from the same activated environment:

```bash
# Install dev deps (incl. RADKit) into your active venv (once)
uv pip install -e ".[dev]"

# Install pre-commit (once)
uv pip install pre-commit    # or: pipx install pre-commit

# Enable the git hook in your clone (once)
pre-commit install

# Optional: run all hooks against the whole tree
pre-commit run --all-files
```

Once installed, the hooks run on every `git commit` and block the commit if
`ruff` or `mypy` report problems (ruff auto-fixes what it can). Because `mypy`
type-checks against the installed RADKit packages, run commits from the
RADKit-enabled environment.

### Project structure

```
radkit_catc_sync/
  __init__.py         # Package entry point and public API
  __main__.py         # python -m radkit_catc_sync support
  cli.py              # Command-line interface and argument parsing
  config.py           # Configuration loading and validation
  models.py           # Data models (CatCDevice, StoredRadkitDevice)
  filters.py          # Device name filtering (FilterSet class)
  catc_client.py      # Catalyst Center HTTP client
  builders.py         # RADKit device model builders
  stats.py            # Statistics tracking
  sync.py             # Core sync logic and reconciliation
tests/
  conftest.py         # Shared test fixtures
  test_*.py           # Test suites by module
```

Each module has a clear responsibility:
- **config.py** — Load and validate settings (immutable `AppConfig` dataclass)
- **filters.py** — Regex-based device name matching (`FilterSet` class)
- **models.py** — Type-safe data structures
- **sync.py** — Core orchestration logic
- **cli.py** — User interaction and I/O
- **catc_client.py**, **builders.py**, **stats.py** — Supporting utilities

## Architecture notes

This project was refactored in v0.2.0 to move from a monolithic 1000-line script to a modular package:

- **Immutable configuration** via `AppConfig` dataclass (no module globals)
- **Type-safe models** instead of stringly-typed dicts
- **Dependency injection** makes testing easier
- **Clear module boundaries** aid maintainability and future expansion

For details, see the [refactoring notes](docs/REFACTORING.md) *(when available)*.

## License

MIT
