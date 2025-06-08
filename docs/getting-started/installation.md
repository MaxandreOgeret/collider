# Installation

## Requirements

- **Python 3.10** or newer
- **Meson 1.8.5** or newer (and Ninja)

Collider installs Meson and Ninja as Python dependencies automatically, but you
can also use system packages if you prefer.

## Install from PyPI

Using [uv](https://docs.astral.sh/uv/) (recommended):

```bash
uv tool install collider-wraps
```

Or with pip:

```bash
pip install collider-wraps
```

Either way, you get the `collider` command-line tool and all runtime dependencies.

## Install from Source

Clone the repository and use [uv](https://docs.astral.sh/uv/) for development:

```bash
git clone git@github.com:MaxandreOgeret/collider.git
cd collider
uv venv
source .venv/bin/activate
uv sync
```

## Verify the Installation

```bash
collider --help
```

You should see the list of available commands.
