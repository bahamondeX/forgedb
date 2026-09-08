"""Single Jinja2 environment for all SQL templates."""

from __future__ import annotations

import os
from typing import Any

from jinja2 import Environment, FileSystemLoader

_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")

_ENVIRONMENT = Environment(loader=FileSystemLoader(_TEMPLATES_DIR), autoescape=False)

_NAMES: set[str] = set()


def render(template_name: str, **context: Any) -> str:
    return _ENVIRONMENT.get_template(template_name).render(**context)


def available_templates() -> set[str]:
    if not _NAMES:
        _NAMES.update(f for f in os.listdir(_TEMPLATES_DIR) if f.endswith(".sql.j2"))
    return set(_NAMES)