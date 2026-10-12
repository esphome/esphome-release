import json
from pathlib import Path

# Configuration options
with Path("config.json").open() as _config_file:
    CONFIG = json.load(_config_file)
