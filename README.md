# RADKit-CatC-Sync

Synchronise Cisco Catalyst Center (formerly DNA Center) device inventory into a [RADKit](https://radkit.cisco.com) service using the RADKit ControlAPI.

## Features

- Imports managed network devices from one or more Catalyst Center clusters
- Creates, updates, and deletes devices in RADKit inventory to stay in sync
- Tracks ownership via `catc_source` metadata — only manages what it imports
- Deletion is scoped: only removes devices from clusters that were synced this run
- Whitelist/blacklist regex filters on device names
- Cross-cluster hostname collision detection (first cluster wins, warning logged)
- `--dry-run` mode to preview all changes before applying them
- `--adopt-existing` to take ownership of manually-added RADKit devices
- `--update-passwords` to refresh SSH credentials on existing devices
- Auto-loads `.env` and `catc_sync.toml` from the script directory or current working directory

## Requirements

- Python 3.11+
- RADKit service installed and running on the same host
- RADKit packages are distributed via Cisco's private PyPI index: `https://radkit.cisco.com/pip`

## Installation

### With uv (recommended)

[uv](https://docs.astral.sh/uv/) automatically picks up the Cisco RADKit index from `pyproject.toml`
(`[tool.uv].extra-index-url`), so no extra flags are needed:

```bash
git clone https://github.com/oboehmer/RADKit-CatC-Sync.git
cd RADKit-CatC-Sync
uv pip install -e .
```

Or install directly without cloning:

```bash
uv pip install git+https://github.com/oboehmer/RADKit-CatC-Sync.git
```

### With pip

pip does not read `[tool.uv]`, so the RADKit index must be passed explicitly:

```bash
git clone https://github.com/oboehmer/RADKit-CatC-Sync.git
cd RADKit-CatC-Sync
pip install --extra-index-url https://radkit.cisco.com/pip -e .
```

Or directly:

```bash
pip install --extra-index-url https://radkit.cisco.com/pip \
    git+https://github.com/oboehmer/RADKit-CatC-Sync.git
```

## Configuration

### 1. Create a config file

Copy the example and edit it for your environment:

```bash
cp catc_sync.toml.example catc_sync.toml
```

At minimum, add your Catalyst Center cluster URLs:

```toml
[catc]
clusters = [
    "https://catc1.example.com",
    "https://catc2.example.com",
]
user = "admin"          # optional (can also use CATC_USER env var)

[radkit]
# base_url = "https://localhost:8081/api/v1"   # default
admin_user = "superadmin"   # optional (can also use RADKIT_ADMIN_USER env var)
ssh_user = "netops"         # optional (can also use RADKIT_SSH_USER env var)

[filters]
whitelist = []              # e.g. ["^router-", "^sw-"]
blacklist = []              # e.g. ["\\.lab\\.", "^test"]
```

See `catc_sync.toml.example` for all available options with documentation.

By default the script looks for `catc_sync.toml` next to the script, then in the current working directory. Use `-c` / `--config` to specify a different path.

### 2. Set passwords via environment variables

Passwords must be provided via environment variables (or a `.env` file). The script loads `.env` from the script directory and the current working directory — in that order. Later files only fill in variables not already set. Usernames can also be set here — environment variables take precedence over config file values.

```dotenv
CATC_PASSWORD=secret
RADKIT_ADMIN_PASSWORD=secret
RADKIT_SSH_PASSWORD=secret
```

| Variable | Description | Required |
|---|---|---|
| `CATC_USER` | Catalyst Center username | If not in config file |
| `CATC_PASSWORD` | Catalyst Center password | Always |
| `RADKIT_ADMIN_USER` | RADKit ControlAPI admin username | If not in config file |
| `RADKIT_ADMIN_PASSWORD` | RADKit ControlAPI admin password | Always |
| `RADKIT_SSH_USER` | SSH username for imported devices | If not in config file |
| `RADKIT_SSH_PASSWORD` | SSH password for imported devices | Always |

## Usage

```
catc-sync [-c CONFIG] [--dry-run] [--update-passwords] [--adopt-existing] [-k] [-v]
```

Or run directly:

```bash
python catc_sync.py [options]
```

### Options

| Flag | Short | Description |
|---|---|---|
| `--config FILE` | `-c` | Path to TOML config file (default: `catc_sync.toml` next to script, then cwd) |
| `--dry-run` | | Preview all changes without applying them |
| `--update-passwords` | | Overwrite SSH password on existing managed devices |
| `--adopt-existing` | `-A` | Take ownership of unmanaged RADKit devices that match a CatC device name |
| `--no-verify-tls` | `-k` | Disable TLS certificate verification for Catalyst Center connections |
| `--verbose` | `-v` | Enable debug logging |

### Examples

```bash
# Preview what would happen
catc-sync --dry-run

# Normal sync
catc-sync

# Sync and refresh SSH passwords
catc-sync --update-passwords

# Take over manually-added devices that now exist in CatC
catc-sync --adopt-existing --dry-run
catc-sync --adopt-existing
```

### Example output

```
12:34:01 INFO     Connecting to RADKit ControlAPI at https://localhost:8081/api/v1
12:34:01 INFO     Fetching existing devices from RADKit...
12:34:01 INFO     Found 42 catc-managed and 3 unmanaged devices in RADKit
12:34:01 INFO     Fetching inventory from 2 CatC cluster(s)...
12:34:03 INFO     Fetched 310 devices from CatC catc1.example.com
12:34:05 INFO     Fetched 280 devices from CatC catc2.example.com
12:34:05 INFO     Total importable devices across all clusters: 585
12:34:05 INFO     Devices to add (or adopt): 543
12:34:05 INFO     Devices to update: 42
12:34:05 INFO     Devices to delete: 3

--- Sync Summary ---
  Added:    543
  Updated:  42
  Adopted:  0  (existing unmanaged devices taken over)
  Deleted:  3
  Skipped:  12
  Errors:   0
```

## How ownership tracking works

Every device imported by this script has a `catc_source` metadata field set to the Catalyst Center hostname (e.g. `catc1.example.com`). This field serves as the ownership marker:

- A non-empty `catc_source` → managed by this script
- Missing or empty `catc_source` → manually added, not touched by default

On each run, the script:
1. Reads all RADKit devices and splits them into managed / unmanaged sets
2. Fetches fresh inventory from all configured CatC clusters
3. Adds devices new to RADKit, updates existing ones, deletes those no longer in CatC
4. Deletion is scoped — only removes a device if its `catc_source` matches one of the clusters synced this run

## Development

```bash
# with uv (index picked up automatically)
uv pip install -e ".[dev]"

# with pip
pip install --extra-index-url https://radkit.cisco.com/pip -e ".[dev]"

ruff check catc_sync.py tests/
ruff format catc_sync.py tests/
mypy catc_sync.py
pytest
```

## License

MIT
