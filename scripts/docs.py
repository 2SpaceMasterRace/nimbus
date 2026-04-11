"""Script to run sphinx-autobuild for live documentation updates."""

# /// script
# dependencies = ["sphinx-autobuild", "sphinx", "furo", "myst-parser"]
# ///
import shutil
import subprocess
import sys

sphinx_autobuild = shutil.which("sphinx-autobuild")
if sphinx_autobuild is None:
    msg = "sphinx-autobuild not found in PATH"
    raise RuntimeError(msg)

sys.exit(subprocess.call([sphinx_autobuild, "docs/source", "docs/build/html"]))  # noqa: S603
