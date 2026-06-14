"""Clouder CLI - Context management commands."""

import sys
import warnings
import yaml

import typer
from rich import print
from rich.table import Table

from ..cloud.ovh.api import get_ovh_projects, get_ovh_project
from ..util.utils import CLOUDER_CONTEXT_FILE, CLOUDER_CONFIG_FOLDER

ctx_app = typer.Typer(no_args_is_help=True)

DEFAULT_BOX_SEPARATOR = ":::"


def _new_context_template() -> dict:
    """Return a default context payload."""
    return {
        "clouder": {
            "version": "1.0.0",
            "default_context": "",
            "current_context": "",
            "contexts": {
                "ovh": {},
                "azure": {},
                "aws": {},
            },
        },
    }


def _ensure_context_shape(context: dict | None) -> dict:
    """Ensure expected keys exist in a context payload."""
    if not context:
        return _new_context_template()
    clouder = context.setdefault("clouder", {})
    clouder.setdefault("version", "1.0.0")
    clouder.setdefault("default_context", "")
    clouder.setdefault("current_context", "")
    contexts = clouder.setdefault("contexts", {})
    contexts.setdefault("ovh", {})
    contexts.setdefault("azure", {})
    contexts.setdefault("aws", {})
    return context


def _sync_discovered_contexts(
    context: dict,
    cloud: str,
    discovered: dict[str, dict[str, str]],
) -> tuple[int, int, int]:
    """Upsert discovered contexts and return (created, updated, unchanged).

    Only discovered context IDs are touched. Existing non-discovered entries are
    preserved as-is.
    """
    cloud_contexts = context["clouder"]["contexts"].setdefault(cloud, {})
    created = 0
    updated = 0
    unchanged = 0

    for context_id, discovered_entry in discovered.items():
        normalized_entry = {"name": str(discovered_entry.get("name") or context_id)}
        for key, value in discovered_entry.items():
            if key == "name":
                continue
            if value in (None, ""):
                continue
            normalized_entry[key] = str(value)

        existing = cloud_contexts.get(context_id)
        if not existing:
            cloud_contexts[context_id] = normalized_entry
            created += 1
            continue

        entry_changed = False
        for key, value in normalized_entry.items():
            if existing.get(key) != value:
                existing[key] = value
                entry_changed = True

        if entry_changed:
            updated += 1
        else:
            unchanged += 1

    return (created, updated, unchanged)


def load_context():
    """Load the clouder context from file."""
    if not CLOUDER_CONTEXT_FILE.is_file():
        typer.echo("You should init a context - run `clouder ctx init`.", err=True)
        raise typer.Exit(1)
    with open(CLOUDER_CONTEXT_FILE, "r") as file:
        context = yaml.safe_load(file)
    return context


def get_current_context():
    """Get the current context (cloud, context_id)."""
    context = load_context()
    current = context["clouder"].get("current_context") or context["clouder"].get("default_context", "")
    if not current:
        typer.echo("No current context set. Run `clouder ctx set <cloud> <context_id>`.", err=True)
        raise typer.Exit(1)
    (cloud, context_id) = current.split(DEFAULT_BOX_SEPARATOR)
    return (cloud, context_id)


def set_default_kubeconfig_path(path: str):
    """Set the default kubeconfig path in the context."""
    context = load_context()
    context["clouder"]["default_kubeconfig_path"] = path
    save_context(context)


def get_default_kubeconfig_path():
    """Get the default kubeconfig path."""
    context = load_context()
    return context["clouder"].get("default_kubeconfig_path", None)


def set_default_ssh_key(key_name: str | None):
    """Set or clear the default SSH key name in the context."""
    context = load_context()
    if key_name:
        context["clouder"]["default_ssh_key"] = key_name
    else:
        context["clouder"].pop("default_ssh_key", None)
    save_context(context)


def get_default_ssh_key() -> str | None:
    """Get the default SSH key name (or None if not set)."""
    context = load_context()
    return context["clouder"].get("default_ssh_key", None)


def save_context(context):
    """Save the context to file."""
    CLOUDER_CONFIG_FOLDER.mkdir(parents=True, exist_ok=True)
    with open(CLOUDER_CONTEXT_FILE, "w") as out:
        yaml.dump(context, out, default_flow_style=False, sort_keys=False)


def print_context(context):
    """Print the context as a rich table."""
    table = Table(title="Clouder Contexts")
    table.add_column("Cloud", justify="left", style="cyan", no_wrap=True)
    table.add_column("Context ID", justify="left", style="green")
    table.add_column("Context Name", justify="left", style="green")
    table.add_column("Current", justify="center", style="green")
    current = context["clouder"].get("current_context") or context["clouder"].get("default_context", "")

    def is_current(cloud, context_id):
        val = cloud + DEFAULT_BOX_SEPARATOR + context_id
        return "*" if current == val else ""

    contexts = context["clouder"]["contexts"]
    for cloud in list(contexts.keys()):
        for context_id in list(contexts[cloud].keys()):
            val = contexts[cloud][context_id]
            table.add_row(
                cloud,
                context_id,
                val["name"],
                is_current(cloud, context_id),
            )
    print(table)


@ctx_app.callback(invoke_without_command=True)
def ctx_default(ctx: typer.Context):
    """Show current context if no subcommand given."""
    if ctx.invoked_subcommand is None:
        context = load_context()
        print_context(context)


@ctx_app.command("init")
def ctx_init():
    """Initialize the context from available cloud projects."""
    clouder = yaml.safe_load(
        """
clouder:
    version: 1.0.0
    default_context:
    current_context:
    contexts:
        ovh:
            pid:
                name: pname
        azure:
        aws:
"""
    )
    try:
        projects = get_ovh_projects()
        for project_id in projects:
            project = get_ovh_project(project_id)
            clouder["clouder"]["contexts"]["ovh"][project_id] = {
                "name": project["description"]
            }
        # Remove the placeholder
        if "pid" in clouder["clouder"]["contexts"]["ovh"]:
            del clouder["clouder"]["contexts"]["ovh"]["pid"]
    except Exception:
        typer.echo("Could not fetch OVH projects (skipping).", err=True)
        clouder["clouder"]["contexts"]["ovh"] = {}
    # --- Azure subscriptions ---
    try:
        from ..cloud.azure.api import list_azure_subscriptions
        subs = list_azure_subscriptions()
        azure_contexts = {}
        for sub in subs:
            azure_contexts[sub["id"]] = {"name": sub["name"]}
        clouder["clouder"]["contexts"]["azure"] = azure_contexts
    except Exception:
        typer.echo("Could not fetch Azure subscriptions (skipping).", err=True)
        clouder["clouder"]["contexts"]["azure"] = {}
    # --- AWS account ---
    try:
        from ..cloud.aws.api import list_aws_accounts
        accounts = list_aws_accounts()
        aws_contexts = {}
        for account in accounts:
            aws_contexts[account["id"]] = {"name": account["name"]}
        clouder["clouder"]["contexts"]["aws"] = aws_contexts
    except Exception:
        typer.echo("Could not fetch AWS account (skipping).", err=True)
        clouder["clouder"]["contexts"]["aws"] = {}
    save_context(clouder)
    context = load_context()
    print_context(context)


@ctx_app.command("sync")
def ctx_sync():
    """Discover OVH/Azure/AWS contexts and create or update local entries."""
    context = load_context() if CLOUDER_CONTEXT_FILE.is_file() else _new_context_template()
    context = _ensure_context_shape(context)

    provider_summaries: list[tuple[str, int, int, int]] = []
    discovered_any = False

    try:
        project_ids = get_ovh_projects()
        discovered = {}
        for project_id in project_ids:
            project = get_ovh_project(project_id)
            iam = project.get("iam") or {}
            discovered[project["project_id"]] = {
                "name": project.get("description") or project["project_id"],
                "iam_id": iam.get("id") or "",
                "iam_urn": iam.get("urn") or "",
            }
        created, updated, unchanged = _sync_discovered_contexts(context, "ovh", discovered)
        provider_summaries.append(("ovh", created, updated, unchanged))
        if discovered:
            discovered_any = True
    except Exception:
        typer.echo("Could not fetch OVH projects (skipping).", err=True)

    try:
        from ..cloud.azure.api import list_azure_subscriptions

        subscriptions = list_azure_subscriptions()
        discovered = {
            sub["id"]: {
                "name": sub["name"],
                "state": sub.get("state", ""),
                "tenant_id": sub.get("tenant_id", ""),
            }
            for sub in subscriptions
        }
        created, updated, unchanged = _sync_discovered_contexts(context, "azure", discovered)
        provider_summaries.append(("azure", created, updated, unchanged))
        if discovered:
            discovered_any = True
    except Exception:
        typer.echo("Could not fetch Azure subscriptions (skipping).", err=True)

    try:
        from ..cloud.aws.api import list_aws_accounts

        accounts = list_aws_accounts()
        discovered = {
            account["id"]: {
                "name": account["name"],
                "arn": account["name"],
            }
            for account in accounts
        }
        created, updated, unchanged = _sync_discovered_contexts(context, "aws", discovered)
        provider_summaries.append(("aws", created, updated, unchanged))
        if discovered:
            discovered_any = True
    except Exception:
        typer.echo("Could not fetch AWS account (skipping).", err=True)

    save_context(context)

    if provider_summaries:
        for cloud, created, updated, unchanged in provider_summaries:
            typer.echo(
                f"Synced {cloud}: {created} created, {updated} updated, {unchanged} unchanged."
            )

    if not discovered_any:
        typer.echo("No OVH, Azure, or AWS contexts discovered.", err=True)

    ctx_list()


@ctx_app.command("ls")
def ctx_list():
    """List persisted cloud contexts in a single merged table.

    Data comes from local context file entries discovered/updated by
    `clouder ctx sync` (or manually set entries).
    """
    # Print the current context first
    try:
        (cloud, context_id) = get_current_context()
        print(f"[bold]Current context:[/bold] [cyan]{cloud}[/cyan] [green]{context_id}[/green]\n")
    except SystemExit:
        print("[dim]No current context set.[/dim]\n")

    context = _ensure_context_shape(load_context())
    current = context["clouder"].get("current_context") or context["clouder"].get("default_context", "")
    contexts = context["clouder"].get("contexts", {})

    table = Table(title="Clouder Contexts")
    table.add_column("Cloud", justify="left", style="cyan", no_wrap=True)
    table.add_column("Context ID", justify="left", style="green")
    table.add_column("Name", justify="left", style="green")
    table.add_column("Details", justify="left", style="yellow")
    table.add_column("Current", justify="center", style="green")

    rows_added = 0
    for cloud_name in ("ovh", "azure", "aws"):
        cloud_contexts = contexts.get(cloud_name, {}) or {}
        for context_id in sorted(cloud_contexts.keys()):
            entry = cloud_contexts.get(context_id, {}) or {}
            name = str(entry.get("name") or context_id)

            details = ""
            if cloud_name == "ovh":
                iam_id = str(entry.get("iam_id") or "")
                iam_urn = str(entry.get("iam_urn") or "")
                detail_parts = []
                if iam_id:
                    detail_parts.append(f"iam_id={iam_id}")
                if iam_urn:
                    detail_parts.append(f"iam_urn={iam_urn}")
                details = ", ".join(detail_parts) if detail_parts else "-"
            elif cloud_name == "azure":
                state = str(entry.get("state") or "")
                tenant_id = str(entry.get("tenant_id") or "")
                detail_parts = []
                if state:
                    detail_parts.append(f"state={state}")
                if tenant_id:
                    detail_parts.append(f"tenant={tenant_id}")
                details = ", ".join(detail_parts) if detail_parts else "-"
            elif cloud_name == "aws":
                arn = str(entry.get("arn") or "")
                details = f"arn={arn}" if arn else "-"

            current_flag = "*" if current == f"{cloud_name}{DEFAULT_BOX_SEPARATOR}{context_id}" else ""
            table.add_row(cloud_name, context_id, name, details, current_flag)
            rows_added += 1

    if rows_added == 0:
        typer.echo(
            "No contexts found. Run `clouder ctx sync` to discover OVH/Azure/AWS contexts.",
            err=True,
        )
        raise typer.Exit(1)

    print(table)


@ctx_app.command("show")
def ctx_show():
    """Show the current context."""
    context = load_context()
    print_context(context)


@ctx_app.command("set")
def ctx_set(
    cloud: str = typer.Argument(..., help="Cloud provider (ovh, azure, aws)."),
    context_id: str = typer.Argument(..., help="The context/project ID."),
):
    """Set the default context."""
    if cloud == "ovh":
        context = get_ovh_project(context_id)
        name = context["description"]
    elif cloud == "azure":
        try:
            from ..cloud.azure.api import list_azure_subscriptions
            subs = list_azure_subscriptions()
            match = next((s for s in subs if s["id"] == context_id), None)
            name = match["name"] if match else context_id
        except Exception:
            name = context_id
    elif cloud == "aws":
        try:
            from ..cloud.aws.api import list_aws_accounts
            accounts = list_aws_accounts()
            match = next((a for a in accounts if a["id"] == context_id), None)
            name = match["name"] if match else context_id
        except Exception:
            name = context_id
    else:
        name = context_id
    clouder = load_context()
    clouder["clouder"]["current_context"] = cloud + DEFAULT_BOX_SEPARATOR + context_id
    if cloud not in clouder["clouder"]["contexts"]:
        clouder["clouder"]["contexts"][cloud] = {}
    clouder["clouder"]["contexts"][cloud][context_id] = {"name": name}
    save_context(clouder)
    print_context(clouder)


@ctx_app.command("rm")
def ctx_remove(
    cloud: str = typer.Argument(..., help="Cloud provider."),
    context_id: str = typer.Argument(..., help="The context/project ID to remove."),
):
    """Remove a context."""
    context = load_context()
    default_context = context["clouder"].get("current_context") or context["clouder"].get("default_context", "")
    if default_context == cloud + DEFAULT_BOX_SEPARATOR + context_id:
        typer.echo("Cannot remove the default context.", err=True)
        raise typer.Exit(1)
    if cloud in context["clouder"]["contexts"] and context_id in context["clouder"]["contexts"][cloud]:
        del context["clouder"]["contexts"][cloud][context_id]
    save_context(context)
    print_context(context)
