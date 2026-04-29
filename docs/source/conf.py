"""Configuration file for the Sphinx documentation builder.

For more information, see:
https://www.sphinx-doc.org/en/master/usage/configuration.html
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _package_dir in (_REPO_ROOT / "src").iterdir():
    if _package_dir.is_dir():
        sys.path.insert(0, str(_package_dir))

os.environ.setdefault("SESSION_SECRET_KEY", "docs-session-secret")
os.environ.setdefault("API_KEY", "docs-storage-api-key")
os.environ.setdefault("AI_SERVER_API_KEY", "docs-ai-api-key")
os.environ.setdefault("AI_SERVER_SIGNING_SECRET", "docs-signing-secret")
os.environ.setdefault("OPENROUTER_API_KEY", "docs-openrouter-key")

# -- Project information -----------------------------------------------------

project = "OSPSD Team 2"
copyright = (  # noqa: A001
    "2026, Hari Varsha V, Ajay Temal, Aarav Agrawal, Daniel J. Barros, Nicholas Maspons"
)
author = "Hari Varsha V, Ajay Temal, Aarav Agrawal, Daniel J. Barros, Nicholas Maspons"
release = "0.1.0"

# -- General configuration ---------------------------------------------------

extensions = [
    "myst_parser",
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.doctest",
    "sphinx.ext.napoleon",
]

myst_enable_extensions = [
    "colon_fence",
    "smartquotes",
    "deflist",
]

default_role = "literal"

templates_path = ["_templates"]
exclude_patterns = ["_build"]
nitpicky = False
autosummary_generate = True
autodoc_member_order = "bysource"
autodoc_typehints = "description"
autodoc_default_options = {
    "members": True,
    "undoc-members": False,
    "show-inheritance": True,
    "exclude-members": "model_config,model_fields",
}

# -- Options for HTML output -------------------------------------------------

html_theme = "furo"
html_title = "OSPSD Team 2 documentation"
html_short_title = "OSPSD Team 2"
html_static_path = ["_static"]
pygments_style = "sphinx"
pygments_dark_style = "monokai"
html_theme_options = {
    "navigation_with_keys": True,
    "source_repository": "https://github.com/2SpaceMasterRace/nimbus/",
    "source_branch": "main",
    "source_directory": "docs/source/",
    "footer_icons": [
        {
            "name": "GitHub",
            "url": "https://github.com/2SpaceMasterRace/nimbus",
            "html": """
                <svg stroke="currentColor" fill="currentColor" stroke-width="0"
                     viewBox="0 0 16 16">
                    <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53
                    5.47 7.59.4.07.55-.17.55-.38
                    0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49
                    -2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15
                    -.68-.52-.01-.53.63-.01 1.08.58 1.23.82
                    .72 1.21 1.87.87 2.33.66.07-.52.28-.87
                    .51-1.07-1.78-.2-3.64-.89-3.64-3.95
                    0-.87.31-1.59.82-2.15-.08-.2-.36-1.02
                    .08-2.12 0 0 .67-.21 2.2.82A7.65 7.65
                    0 0 1 8 4.58c.68 0 1.36.09 2 .26
                    1.53-1.04 2.2-.82 2.2-.82.44 1.1
                    .16 1.92.08 2.12.51.56.82 1.27.82
                    2.15 0 3.07-1.87 3.75-3.65 3.95
                    .29.25.54.73.54 1.48
                    0 1.07-.01 1.93-.01 2.2
                    0 .21.15.46.55.38A8.013 8.013
                    0 0 0 16 8c0-4.42-3.58-8-8-8z"/>
                </svg>
            """,
            "class": "",
        },
    ],
}
html_css_files = ["docs-layout.css"]
html_sidebars = {
    "**": [
        "sidebar/brand.html",
        "sidebar/search.html",
        "sidebar/scroll-start.html",
        "sidebar/navigation.html",
        "sidebar/scroll-end.html",
    ],
}
