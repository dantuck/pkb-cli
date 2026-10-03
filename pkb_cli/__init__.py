"""pkb-cli: the `kb` command-line tool for a Diataxis-based personal knowledge base."""
from importlib import metadata

try:
    __version__ = metadata.version("pkb-cli")
except metadata.PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0.0.0+unknown"
