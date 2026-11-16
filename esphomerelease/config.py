import json
import os
import sys
from pathlib import Path
from typing import Any

from .exceptions import ConfigNotFoundError

CONFIG_FILE = "config.json"


def find_nearby_config_dirs(cwd: Path) -> list[Path]:
    """Directories one level around ``cwd`` (parent, siblings, children) holding a config.json."""
    parent = cwd.parent
    candidates = [parent]
    candidates.extend(d for d in parent.iterdir() if d != cwd and d.is_dir())
    candidates.extend(d for d in cwd.iterdir() if d.is_dir())
    return sorted({d for d in candidates if (d / CONFIG_FILE).is_file()})


def find_install_config_dirs() -> list[Path]:
    """Directories derived from where espr is installed that hold a config.json.

    That is the repo root for a source/editable install (two levels above this
    file) and, when running in a venv, the folder containing the venv.
    """
    candidates = [Path(__file__).resolve().parent.parent]
    if sys.prefix != sys.base_prefix:
        candidates.append(Path(sys.prefix).resolve().parent)
    return [d for d in candidates if (d / CONFIG_FILE).is_file()]


def load_config() -> dict[str, Any]:
    cwd = Path.cwd()
    config_path = cwd / CONFIG_FILE
    if not config_path.is_file():
        install_dirs = find_install_config_dirs()
        if install_dirs:
            # Module location wins over the venv parent. Other files (users_cache.json,
            # templates) are read relative to the cwd, so really switch to it.
            target = install_dirs[0]
            os.chdir(target)
            print(f"No {CONFIG_FILE} in {cwd} - running from {target} instead.", file=sys.stderr)
            config_path = target / CONFIG_FILE
        else:
            nearby = find_nearby_config_dirs(cwd)
            if nearby:
                dirs = "\n".join(f"  {d}" for d in nearby)
                message = f"No {CONFIG_FILE} in {cwd}. You are in the wrong directory - run espr from:\n{dirs}"
            else:
                message = (
                    f"No {CONFIG_FILE} in {cwd}, its parent, sibling or child directories, "
                    "or the espr install location. "
                    "Run espr from the esphome-release folder "
                    "(see README.md to create config.json)."
                )
            raise ConfigNotFoundError(message) from None
    with config_path.open() as config_file:
        return json.load(config_file)


# Configuration options
CONFIG = load_config()
