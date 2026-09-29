# SPDX-FileCopyrightText: 2026 NXLX and contributors
# SPDX-License-Identifier: Apache-2.0
"""Themes are token files: seven colours in a small JSON file.

Built-in themes live in pvj/themes.d; user themes are dropped into the add-ons
folder (<addons>/themes), which updates never touch. Colours are strictly
validated as #rrggbb so a theme file can never inject CSS.
"""

import glob
import json
import os
import re

TOKENS = ("bg", "cd", "fg", "ln", "mu", "ac", "on")
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_ID = re.compile(r"^[a-z][a-z0-9-]{1,40}$")
BUILTIN_DIR = os.path.join(os.path.dirname(__file__), "themes.d")


class ThemeError(Exception):
    pass


def luminance(hex_colour):
    def channel(v):
        v = v / 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hex_colour[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a, b):
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def text_on(accent):
    """Black or white, whichever reads better on the accent colour."""
    return "#000000" if contrast(accent, "#000000") >= contrast(accent, "#ffffff") else "#ffffff"


def validate(theme):
    problems = []
    if not isinstance(theme, dict):
        return ["theme is not an object"]
    if not isinstance(theme.get("id"), str) or not _ID.match(theme["id"]):
        problems.append("bad id")
    if not isinstance(theme.get("name"), str) or not theme.get("name") or len(theme["name"]) > 40:
        problems.append("bad name")
    tokens = theme.get("tokens")
    if not isinstance(tokens, dict):
        return problems + ["tokens missing"]
    for t in TOKENS:
        if not isinstance(tokens.get(t), str) or not _HEX.match(tokens[t]):
            problems.append("token %s must be #rrggbb" % t)
    extra = set(tokens) - set(TOKENS)
    if extra:
        problems.append("unknown tokens: %s" % ", ".join(sorted(extra)))
    return problems


def _load_dir(directory, source, out, strict):
    for path in sorted(glob.glob(os.path.join(directory, "*.json"))):
        try:
            with open(path) as f:
                theme = json.load(f)
            problems = validate(theme)
        except (OSError, ValueError) as e:
            problems = [str(e)]
            theme = None
        if problems:
            if strict:
                raise ThemeError("%s: %s" % (os.path.basename(path), "; ".join(problems)))
            continue  # a broken add-on theme is skipped, never fatal
        if theme["id"] in out and source == "addon":
            continue  # add-ons cannot replace built-in themes
        out[theme["id"]] = dict(theme, source=source)


def load_themes(addons_dir=None):
    themes = {}
    _load_dir(BUILTIN_DIR, "builtin", themes, strict=True)
    if addons_dir:
        _load_dir(os.path.join(addons_dir, "themes"), "addon", themes, strict=False)
    return themes


def css(theme, accent=None):
    """CSS custom properties for a theme, with an optional validated accent override."""
    tokens = dict(theme["tokens"])
    if accent is not None:
        if not isinstance(accent, str) or not _HEX.match(accent):
            raise ThemeError("accent must be #rrggbb")
        tokens["ac"] = accent.lower()
        tokens["on"] = text_on(accent)
    return ":root{%s}" % ";".join("--%s:%s" % (k, tokens[k]) for k in TOKENS)
