"""Run mypy over ``tests/typing/typed_api.py`` and check the inferred types."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("mypy")

ROOT = Path(__file__).resolve().parent.parent

EXPECTED = [
    'Revealed type is "forgedb.orm.query.Query[typed_api.Post]"',  # Post.filter(...)
    'Revealed type is "list[typed_api.Post]"',  # Query.all()
    'Revealed type is "typed_api.Post | None"',  # Query.first()
    'Revealed type is "list[typed_api.Post]"',  # Post.all()
    'Revealed type is "typed_api.Post | None"',  # Post.get()
    'Revealed type is "typed_api.Post | None"',  # Post.first()
    'Revealed type is "typed_api.Post"',  # Post.create()
    'Revealed type is "typed_api.Post | None"',  # Post.update()
    'Revealed type is "typed_api.Post"',  # post.save()
    'Revealed type is "typed_api.Post | None"',  # post.refresh()
    'Revealed type is "list[dict[str, Any]]"',  # Query.values()
    'Revealed type is "typed_api.User"',  # post.author()
    'Revealed type is "list[typed_api.Post]"',  # user.posts()
]


@pytest.fixture(scope="module")
def mypy_output() -> str:
    result = subprocess.run(
        [sys.executable, "-m", "mypy", "--no-incremental"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout


def test_reveal_types(mypy_output: str) -> None:
    revealed = [line.split("note: ", 1)[1] for line in mypy_output.splitlines() if "Revealed type" in line]
    assert revealed == EXPECTED, mypy_output


def test_no_type_errors(mypy_output: str) -> None:
    errors = [line for line in mypy_output.splitlines() if ": error:" in line]
    assert errors == [], mypy_output
