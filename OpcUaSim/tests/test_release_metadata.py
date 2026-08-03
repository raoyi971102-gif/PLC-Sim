from __future__ import annotations

import ast
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 test compatibility.
    import tomli as tomllib


PROJECT_DIRECTORY = Path(__file__).parents[1]


def test_package_version_sources_match() -> None:
    project = tomllib.loads(
        (PROJECT_DIRECTORY / "pyproject.toml").read_text(encoding="utf-8")
    )
    module = ast.parse(
        (PROJECT_DIRECTORY / "__init__.py").read_text(encoding="utf-8")
    )
    version_assignment = next(
        statement
        for statement in module.body
        if isinstance(statement, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "__version__"
            for target in statement.targets
        )
    )
    assert isinstance(version_assignment.value, ast.Constant)

    assert project["project"]["version"] == version_assignment.value.value


def test_one_click_requirements_use_native_release_constraints() -> None:
    requirements = _requirement_lines(PROJECT_DIRECTORY / "requirements.txt")
    constraints = _requirement_lines(
        PROJECT_DIRECTORY / "packaging" / "constraints.txt"
    )

    assert requirements <= constraints


def _requirement_lines(path: Path) -> set[str]:
    return {
        line
        for raw_line in path.read_text(encoding="utf-8").splitlines()
        if (line := raw_line.strip()) and not line.startswith("#")
    }
