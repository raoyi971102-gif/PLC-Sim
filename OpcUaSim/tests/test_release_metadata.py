from __future__ import annotations

import ast
import subprocess
import sys
import tomllib
from pathlib import Path

PROJECT_DIRECTORY = Path(__file__).parents[1]
REPOSITORY_DIRECTORY = PROJECT_DIRECTORY.parent


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


def test_package_supports_only_python_311() -> None:
    project = tomllib.loads(
        (PROJECT_DIRECTORY / "pyproject.toml").read_text(encoding="utf-8")
    )

    assert project["project"]["requires-python"] == ">=3.11,<3.12"
    assert project["project"]["optional-dependencies"]["test"] == ["pytest>=8.0"]


def test_launchers_require_an_exact_python_311_interpreter() -> None:
    unix_launcher = (PROJECT_DIRECTORY / "scripts" / "unix_common.sh").read_text(
        encoding="utf-8"
    )
    windows_launcher = (
        PROJECT_DIRECTORY / "scripts" / "find_python.bat"
    ).read_text(encoding="utf-8")

    exact_version_check = "sys.version_info[:2] == (3, 11)"
    assert exact_version_check in unix_launcher
    assert "for candidate in python3.11 python3; do" in unix_launcher
    assert exact_version_check in windows_launcher
    assert "py -3.11" in windows_launcher


def test_ci_uses_only_python_311() -> None:
    installer_workflow = (
        REPOSITORY_DIRECTORY / ".github" / "workflows" / "installers.yml"
    ).read_text(encoding="utf-8")
    deploy_workflow = (
        REPOSITORY_DIRECTORY / ".github" / "workflows" / "deploy.yml"
    ).read_text(encoding="utf-8")

    assert "python-version: ['3.11']" in installer_workflow
    assert "python-version: '3.10'" not in installer_workflow
    assert "python-version: '3.11'" in deploy_workflow
    assert "python-version: '3.10'" not in deploy_workflow


def test_release_builds_and_publishes_linux_installers() -> None:
    """验证原生 Release 包含 Linux x64 构建、校验和发布依赖。

    参数：无；读取仓库中的安装包工作流。
    返回：无；断言 DEB、便携包及 Release 汇总任务均已声明。
    """

    installer_workflow = (
        REPOSITORY_DIRECTORY / ".github" / "workflows" / "installers.yml"
    ).read_text(encoding="utf-8")

    assert "  linux:\n" in installer_workflow
    assert "runs-on: ubuntu-22.04" in installer_workflow
    assert "packaging/build_linux_packages.sh" in installer_workflow
    assert "OpcUaSim-Linux-x64-v${version}.deb" in installer_workflow
    assert "OpcUaSim-Linux-x64-v${version}.tar.gz" in installer_workflow
    assert "needs: [python-package, windows, linux, macos]" in installer_workflow


def test_project_version_reader_matches_package_metadata() -> None:
    """验证 Release 标签校验脚本读取当前 Python 包版本。

    参数：无；调用仓库中的版本读取脚本。
    返回：无；断言脚本输出与 0.2.6 发布版本一致。
    """

    result = subprocess.run(
        [
            sys.executable,
            str(PROJECT_DIRECTORY / "packaging" / "project_version.py"),
            str(PROJECT_DIRECTORY / "pyproject.toml"),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == "0.2.6"


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
