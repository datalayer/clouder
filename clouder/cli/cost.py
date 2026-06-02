# Copyright (c) 2023-2026 Datalayer, Inc.
#
# Datalayer License

"""Clouder cost commands."""

from __future__ import annotations

import typer

from ..cloud.base import NodeType


cost_app = typer.Typer(help="Cost and SKU pricing helpers.")


def _fallback_node_types(cloud: str) -> list[NodeType]:
    if cloud == "aws":
        return [
            NodeType(name="t3.large", vcpu=2, memory_gb=8.0, hourly_usd=None),
            NodeType(name="m6i.large", vcpu=2, memory_gb=8.0, hourly_usd=None),
        ]
    return [
        NodeType(name="Standard_B2s", vcpu=2, memory_gb=4.0, hourly_usd=None),
        NodeType(name="Standard_D4s_v5", vcpu=4, memory_gb=16.0, hourly_usd=None),
    ]


@cost_app.command("list")
def list_costs(
    cloud: str = typer.Option("azure", help="Cloud provider (azure|aws)."),
    region: str = typer.Option("westeurope", help="Cloud region."),
):
    """List known node types and hourly costs when available."""

    _ = region
    cloud_name = cloud.strip().lower()
    if cloud_name not in {"azure", "aws"}:
        raise typer.BadParameter("cloud must be one of: azure, aws")

    typer.echo(f"Cloud: {cloud_name}")
    typer.echo("SKU\tvcpu\tmemory_gb\thourly_usd")
    for sku in _fallback_node_types(cloud_name):
        hourly = "n/a" if sku.hourly_usd is None else f"{sku.hourly_usd:.6f}"
        typer.echo(f"{sku.name}\t{sku.vcpu}\t{sku.memory_gb}\t{hourly}")
