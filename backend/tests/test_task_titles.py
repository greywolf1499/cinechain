"""Every submitted task has a player-facing title in the shared frontend feed."""

import ast
import re
from pathlib import Path


def test_task_titles_cover_all_submitted_names():
    root = Path(__file__).resolve().parents[2]
    frontend = (root / "frontend/src/lib/tasks.ts").read_text()
    titles = set(
        re.findall(r"^\s*(\w+):\s*\"", frontend.split("export const TASK_TITLES")[1], re.MULTILINE)
    )
    names = set()
    for source in (root / "backend/app").rglob("*.py"):
        tree = ast.parse(source.read_text())
        constants = {
            target.id: node.value.value
            for node in tree.body
            if isinstance(node, ast.Assign)
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        mappings = {
            target.id: [value.value for value in node.value.values]
            for node in tree.body
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict)
            if all(
                isinstance(value, ast.Constant) and isinstance(value.value, str)
                for value in node.value.values
            )
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            symbol = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else (node.func.id if isinstance(node.func, ast.Name) else None)
            )
            if symbol != "submit_task":
                continue
            argument = (
                node.args[2]
                if len(node.args) > 2
                else next(keyword.value for keyword in node.keywords if keyword.arg == "name")
            )
            if (
                isinstance(argument, ast.Subscript)
                and isinstance(argument.value, ast.Name)
                and argument.value.id in mappings
            ):
                # A literal name table: every value it can produce must have a title.
                names.update(mappings[argument.value.id])
                continue
            name = (
                argument.value
                if isinstance(argument, ast.Constant)
                else constants.get(argument.id if isinstance(argument, ast.Name) else "")
            )
            assert isinstance(name, str), f"Unresolved task name in {source}"
            names.add(name)
    assert names
    assert names <= titles, f"Missing task titles: {sorted(names - titles)}"
