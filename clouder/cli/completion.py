"""Clouder CLI - explicit shell completion helpers."""

from __future__ import annotations

import os
from pathlib import Path

import typer
from rich import print
from rich.panel import Panel


completion_app = typer.Typer(no_args_is_help=True)


def _bash_snippet() -> str:
    return (
        "# >>> clouder completion >>>\n"
        "eval \"$(c --show-completion bash)\"\n"
        "eval \"$(clouder --show-completion bash)\"\n"
        "# <<< clouder completion <<<\n"
    )


def _zsh_snippet() -> str:
    return (
        "# >>> clouder completion >>>\n"
        "eval \"$(c --show-completion zsh)\"\n"
        "eval \"$(clouder --show-completion zsh)\"\n"
        "# <<< clouder completion <<<\n"
    )


def _fish_snippet() -> str:
    return (
        "# >>> clouder completion >>>\n"
        "c --show-completion fish | source\n"
        "clouder --show-completion fish | source\n"
        "# <<< clouder completion <<<\n"
    )


def _rc_path(shell: str) -> Path:
    home = Path.home()
    if shell == "bash":
        return home / ".bashrc"
    if shell == "zsh":
        return home / ".zshrc"
    if shell == "fish":
        return home / ".config" / "fish" / "config.fish"
    raise typer.BadParameter(f"Unsupported shell: {shell}")


def _snippet_for(shell: str) -> str:
    if shell == "bash":
        return _bash_snippet()
    if shell == "zsh":
        return _zsh_snippet()
    if shell == "fish":
        return _fish_snippet()
    raise typer.BadParameter(f"Unsupported shell: {shell}")


def _upsert_snippet(rc_file: Path, snippet: str) -> str:
    """Insert or replace managed completion block in an RC file.

    Returns one of: created, updated, unchanged.
    """
    rc_file.parent.mkdir(parents=True, exist_ok=True)
    current = rc_file.read_text() if rc_file.exists() else ""
    begin_marker = "# >>> clouder completion >>>"
    end_marker = "# <<< clouder completion <<<"

    if begin_marker in current and end_marker in current:
        start = current.index(begin_marker)
        end = current.index(end_marker, start) + len(end_marker)
        replacement = snippet.rstrip("\n")
        updated = current[:start] + replacement + current[end:]
        updated = updated if updated.endswith("\n") else updated + "\n"
        if updated == current:
            return "unchanged"
        rc_file.write_text(updated)
        return "updated"

    with rc_file.open("a") as f:
        if current and not current.endswith("\n"):
            f.write("\n")
        f.write("\n" + snippet)
    return "created"


def _detect_shell() -> str:
    shell_env = os.environ.get("SHELL", "")
    name = Path(shell_env).name
    if name in {"bash", "zsh", "fish"}:
        return name
    return "bash"


@completion_app.command("show")
def completion_show(
    shell: str = typer.Argument(..., help="Shell name: bash, zsh, or fish."),
):
    """Print the completion snippet for a shell."""
    typer.echo(_snippet_for(shell))


@completion_app.command("install")
def completion_install(
    shell: str = typer.Option("", "--shell", help="Shell name: bash, zsh, or fish. Auto-detected when omitted."),
):
    """Install completion for both `clouder` and `c` in your shell RC file."""
    resolved_shell = shell or _detect_shell()
    snippet = _snippet_for(resolved_shell)
    updated_files = []

    if resolved_shell == "bash":
        # VS Code terminals are often login shells that read ~/.bash_profile,
        # while regular interactive shells read ~/.bashrc.
        for rc_file in (Path.home() / ".bashrc", Path.home() / ".bash_profile"):
            result = _upsert_snippet(rc_file, snippet)
            if result != "unchanged":
                updated_files.append(f"{rc_file} ({result})")
    else:
        rc_file = _rc_path(resolved_shell)
        result = _upsert_snippet(rc_file, snippet)
        if result != "unchanged":
            updated_files.append(f"{rc_file} ({result})")

    if updated_files:
        files_text = "\n".join(f"- {entry}" for entry in updated_files)
        print(
            Panel(
                f"Updated completion setup for shell: {resolved_shell}\n\n"
                f"{files_text}\n\n"
                "Open a new terminal session, or source the relevant RC file.",
                title="Clouder Completion",
            )
        )
        return

    print(Panel("Completion already up to date.", title="Clouder Completion"))
