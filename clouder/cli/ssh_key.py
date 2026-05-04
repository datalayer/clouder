"""Clouder CLI - SSH key management commands."""

import subprocess

import typer
from rich import print
from rich.table import Table

from .ctx import get_current_context, get_default_ssh_key, set_default_ssh_key
from ..util.utils import SSH_PUBLIC_KEY, SSH_FOLDER
from ..cloud.local.api import get_local_ssh_keys

ssh_key_app = typer.Typer(no_args_is_help=True)


@ssh_key_app.callback(invoke_without_command=True)
def ssh_key_default(ctx: typer.Context):
    """List SSH keys if no subcommand given."""
    if ctx.invoked_subcommand is None:
        ssh_key_list()


@ssh_key_app.command("create")
def ssh_key_create(
    name: str = typer.Argument(..., help="Name for the SSH key."),
    key_type: str = typer.Option("ed25519", "--type", "-t", help="Key type: ed25519, rsa."),
):
    """Create an SSH key pair locally (and register in cloud if supported)."""
    key_path = SSH_FOLDER / name
    if key_path.exists():
        typer.echo(f"Key '{name}' already exists at {key_path}.", err=True)
        raise typer.Exit(1)
    SSH_FOLDER.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ssh-keygen", "-t", key_type, "-f", str(key_path), "-N", "", "-C", f"clouder-{name}"],
        check=True,
    )
    key_path.chmod(0o600)
    print(f"[green]Key pair created: {key_path}[/green]")

    # Register in cloud if OVH
    (cloud, context_id) = get_current_context()
    if cloud == "ovh":
        public_key = (SSH_FOLDER / f"{name}.pub").read_text().strip()
        from ..cloud.ovh.api import create_ovh_ssh_key
        res = create_ovh_ssh_key(context_id, name, public_key)
        print(res)


@ssh_key_app.command("ls")
def ssh_key_list():
    """List SSH keys (local and cloud)."""
    # Local keys
    table = Table(title="SSH Keys Local")
    table.add_column("Name", justify="left", style="cyan", no_wrap=True)
    table.add_column("Path", justify="left", style="dim")
    local_keys = get_local_ssh_keys()
    for key_name in local_keys:
        table.add_row(key_name, str(SSH_FOLDER / key_name))
    print(table)
    # Show current default
    default_key = get_default_ssh_key()
    if default_key:
        print(f"\n[bold]Default SSH key:[/bold] [cyan]{default_key}[/cyan]")
    # Cloud keys
    (cloud, context_id) = get_current_context()
    if cloud == "azure":
        print("[dim]Azure does not have a cloud SSH key registry. Showing local keys only.[/dim]")
    else:
        from ..cloud.ovh.api import get_ovh_ssh_keys, get_ovh_project
        project = get_ovh_project(context_id)
        ssh_keys = get_ovh_ssh_keys(context_id)
        table = Table(title=f"SSH Keys {cloud}:{project['description']}")
        table.add_column("ID", justify="left", style="cyan", no_wrap=True)
        table.add_column("Name", justify="left", style="cyan")
        table.add_column("Fingerprint", justify="left", style="green")
        table.add_column("Public Key", justify="left", style="green")
        for ssh_key in ssh_keys:
            table.add_row(
                ssh_key["id"],
                ssh_key["name"],
                ssh_key.get("fingerprint", ""),
                ssh_key.get("publicKey", ""),
            )
        print(table)


@ssh_key_app.command("set-current")
def ssh_key_set_current():
    """Set or clear the default SSH key used by all commands.

    Lists available SSH keys in ~/.ssh/ and lets you pick one as the
    default.  The choice is persisted in ~/.clouder/clouder.yaml and
    used automatically by 'clouder ssh connect', 'clouder kubeadm' commands,
    etc. — unless overridden with --key/-i.
    """
    from rich.prompt import Prompt

    local_keys = get_local_ssh_keys()
    if not local_keys:
        typer.echo("No SSH keys found in ~/.ssh/.", err=True)
        raise typer.Exit(1)

    current = get_default_ssh_key()

    print("\n[bold]SSH keys:[/bold]")
    for i, kn in enumerate(local_keys, 1):
        marker = " [green](current default)[/green]" if kn == current else ""
        print(f"  {i}. {kn}{marker}")
    clear_idx = len(local_keys) + 1
    print(f"  {clear_idx}. [dim](clear default)[/dim]")

    default_choice = str(local_keys.index(current) + 1) if current and current in local_keys else "1"
    choice = Prompt.ask(
        "\nSelect SSH key number or type key name",
        default=default_choice,
    )

    if choice.isdigit():
        idx = int(choice)
        if idx == clear_idx:
            set_default_ssh_key(None)
            print("[green]Default SSH key cleared.[/green]")
            return
        if 1 <= idx <= len(local_keys):
            selected = local_keys[idx - 1]
        else:
            typer.echo(f"Invalid choice: {choice}", err=True)
            raise typer.Exit(1)
    else:
        # Typed a key name
        if (SSH_FOLDER / choice).exists():
            selected = choice
        else:
            typer.echo(f"Key '{choice}' not found in {SSH_FOLDER}.", err=True)
            raise typer.Exit(1)

    set_default_ssh_key(selected)
    print(f"[green]Default SSH key set to:[/green] [cyan]{selected}[/cyan]")
    print(f"[dim]Stored in ~/.clouder/clouder.yaml[/dim]")


@ssh_key_app.command("download")
def ssh_key_download(
    name: str = typer.Argument(..., help="Name to save the key as (e.g. my-server)."),
    private_key: str = typer.Option(None, "--key", "-k", help="Private key content or path to a file containing it."),
):
    """Download/save a private key to ~/.ssh/ with correct permissions."""
    key_path = SSH_FOLDER / name
    if key_path.exists():
        from rich.prompt import Confirm
        if not Confirm.ask(f"Key '{name}' already exists at {key_path}. Overwrite?", default=False):
            raise typer.Exit(0)

    if private_key and not private_key.startswith("-----"):
        # It's a file path
        from pathlib import Path
        src = Path(private_key).expanduser()
        if not src.is_file():
            typer.echo(f"File not found: {private_key}", err=True)
            raise typer.Exit(1)
        content = src.read_text()
    elif private_key:
        content = private_key
    else:
        typer.echo("Paste the private key content below (end with Ctrl+D or empty line):")
        import sys
        lines = []
        try:
            for line in sys.stdin:
                if line.strip() == "" and lines and lines[-1].strip() == "":
                    break
                lines.append(line)
        except EOFError:
            pass
        content = "".join(lines)

    if not content.strip():
        typer.echo("No key content provided.", err=True)
        raise typer.Exit(1)

    SSH_FOLDER.mkdir(parents=True, exist_ok=True)
    key_path.write_text(content)
    key_path.chmod(0o600)
    print(f"[green]Private key saved to {key_path} (permissions: 600)[/green]")
    typer.echo(f"\nUsage: ssh -i {key_path} user@host")
