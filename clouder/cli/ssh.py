"""Clouder CLI - SSH into a virtual machine."""

import os
import subprocess
from typing import Optional

import typer
from rich import print
from rich.prompt import Prompt

from .ctx import get_current_context, get_default_ssh_key
from ..util.utils import SSH_FOLDER
from ..cloud.local.api import get_local_ssh_keys

ssh_app = typer.Typer(no_args_is_help=True)


@ssh_app.callback(invoke_without_command=True)
def ssh_default(
    ctx: typer.Context,
    vm_name: Optional[str] = typer.Argument(None, help="Name of the virtual machine to SSH into."),
    user: str = typer.Option(None, "--user", "-u", help="SSH username."),
    key: str = typer.Option(None, "--key", "-i", help="SSH key name (from ~/.ssh/)."),
    port: int = typer.Option(22, "--port", "-p", help="SSH port."),
    command: str = typer.Option(None, "--command", "-c", help="Command to run on the remote host (non-interactive)."),
):
    """SSH into a virtual machine by name."""
    if ctx.invoked_subcommand is not None:
        return
    if not vm_name:
        typer.echo("Usage: clouder ssh <vm-name>")
        raise typer.Exit(1)

    (cloud, context_id) = get_current_context()

    # Resolve the VM's IP address
    ip = _resolve_vm_ip(cloud, context_id, vm_name)
    if not ip:
        typer.echo(f"Could not determine IP address for VM '{vm_name}'.", err=True)
        raise typer.Exit(1)

    # Default user
    if not user:
        if cloud == "azure":
            user = "azureuser"
        else:
            user = "ubuntu"

    # Resolve SSH key
    key_path = _resolve_ssh_key(key, vm_name)

    # Build SSH command
    ssh_cmd = ["ssh"]
    if key_path:
        ssh_cmd.extend(["-i", str(key_path)])
    ssh_cmd.extend(["-p", str(port), "-o", "StrictHostKeyChecking=accept-new", f"{user}@{ip}"])

    if command:
        ssh_cmd.append(command)

    print(f"[bold]Connecting to {vm_name} ({ip})...[/bold]")
    print(f"[dim]{' '.join(ssh_cmd)}[/dim]\n")

    if command:
        # Run command and return exit code instead of replacing process
        result = subprocess.run(ssh_cmd)
        raise typer.Exit(result.returncode)

    # Replace process with interactive SSH
    os.execvp("ssh", ssh_cmd)


def _resolve_vm_ip(cloud: str, context_id: str, vm_name: str) -> str:
    """Resolve the public IP of a VM by name."""
    if cloud == "azure":
        from ..cloud.azure.api import list_azure_vms, get_azure_vm_public_ip
        vms = list_azure_vms(subscription_id=context_id)
        match = [vm for vm in vms if vm["name"] == vm_name]
        if not match:
            typer.echo(f"VM '{vm_name}' not found.", err=True)
            raise typer.Exit(1)
        vm = match[0]
        rg = vm["resource_group"]
        ip = get_azure_vm_public_ip(rg, vm_name, subscription_id=context_id)
        return ip
    elif cloud == "aws":
        from ..cloud.aws.api import list_aws_vms
        vms = list_aws_vms()
        match = [vm for vm in vms if vm["name"] == vm_name]
        if not match:
            typer.echo(f"VM '{vm_name}' not found.", err=True)
            raise typer.Exit(1)
        return match[0].get("public_ip")
    else:
        from ..cloud.ovh.api import get_ovh_vm
        vms = get_ovh_vm(context_id)
        match = [vm for vm in vms if vm["name"] == vm_name]
        if not match:
            typer.echo(f"VM '{vm_name}' not found.", err=True)
            raise typer.Exit(1)
        vm = match[0]
        # OVH VMs have ipAddresses
        for addr in vm.get("ipAddresses", []):
            if addr.get("version") == 4 and addr.get("type") == "public":
                return addr["ip"]
        return None


def _resolve_ssh_key(key_name: str, vm_name: str) -> str:
    """Resolve the SSH key path, prompting if needed."""
    if key_name:
        path = SSH_FOLDER / key_name
        if path.exists():
            return str(path)
        typer.echo(f"SSH key '{key_name}' not found at {path}.", err=True)
        raise typer.Exit(1)

    # Try to find a key matching the VM name
    vm_key = SSH_FOLDER / f"{vm_name}-key"
    if vm_key.exists():
        print(f"[dim]Using SSH key: {vm_key}[/dim]")
        return str(vm_key)

    # Check for a configured default key
    default_key = get_default_ssh_key()
    if default_key:
        default_path = SSH_FOLDER / default_key
        if default_path.exists():
            print(f"[dim]Using default SSH key: {default_path}[/dim]")
            return str(default_path)

    # List available keys and let user pick
    local_keys = get_local_ssh_keys()
    if not local_keys:
        typer.echo("No SSH keys found in ~/.ssh/. Connecting without key.", err=True)
        return None

    if len(local_keys) == 1:
        key_path = SSH_FOLDER / local_keys[0]
        print(f"[dim]Using SSH key: {key_path}[/dim]")
        return str(key_path)

    print("\n[bold]SSH keys:[/bold]")
    for i, kn in enumerate(local_keys, 1):
        typer.echo(f"  {i}. {kn}")
    choice = Prompt.ask("Select SSH key number or type key name", default="1")
    if choice.isdigit() and 1 <= int(choice) <= len(local_keys):
        return str(SSH_FOLDER / local_keys[int(choice) - 1])
    path = SSH_FOLDER / choice
    if path.exists():
        return str(path)
    typer.echo(f"Key '{choice}' not found.", err=True)
    raise typer.Exit(1)
