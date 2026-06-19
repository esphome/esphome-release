import json
import os


def _load_config() -> dict:
    """Load configuration from config.json.

    A missing config.json is tolerated so the package stays importable without
    a configured working copy (e.g. for ``--help`` or the test suite). A present
    but malformed config.json still raises loudly — that is a real
    misconfiguration the operator must fix.
    """
    if not os.path.exists("config.json"):
        return {}
    with open("config.json") as f:
        return json.load(f)


# Configuration options
CONFIG = _load_config()
