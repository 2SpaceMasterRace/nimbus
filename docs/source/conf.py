"""Configuration file for the Sphinx documentation builder.

For more information, see:
https://www.sphinx-doc.org/en/master/usage/configuration.html
"""

# -- Project information -----------------------------------------------------

project = "OSPSD Team 2"
copyright = (  # noqa: A001
    "2026, Hari Varsha V, Ajay Temal, Aarav Agrawal, Daniel J. Barros, Nicholas Maspons"
)
author = "Hari Varsha V, Ajay Temal, Aarav Agrawal, Daniel J. Barros, Nicholas Maspons"

# -- General configuration ---------------------------------------------------

extensions = ["myst_parser"]

myst_enable_extensions = [
    "colon_fence",
    "smartquotes",
    "deflist",
]

default_role = "any"

templates_path = ["_templates"]
exclude_patterns = ["_build"]

# -- Options for HTML output -------------------------------------------------

html_theme = "furo"
html_static_path = ["_static"]
html_theme_options = {
    "navigation_with_keys": True,
}
html_sidebars = {
    "**": [
        "sidebar/brand.html",
        "sidebar/search.html",
        "sidebar/scroll-start.html",
        "sidebar/navigation.html",
        "sidebar/scroll-end.html",
    ],
}
