# Copyright (c) 2023-2026 Datalayer, Inc.
#
# Datalayer License

"""Cloud provider abstractions used by scaling integrations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class NodeType:
    """Provisionable node SKU description."""

    name: str
    vcpu: int
    memory_gb: float
    gpu_count: int = 0
    gpu_type: str | None = None
    hourly_usd: float | None = None


@dataclass(frozen=True)
class NodeRef:
    """Cloud node identifier."""

    cloud: str
    region: str
    node_id: str
    name: str


@dataclass(frozen=True)
class NodeSpec:
    """Node creation request."""

    name: str
    region: str
    node_type: str


@dataclass(frozen=True)
class NodeStatus:
    """Cloud node lifecycle status."""

    ref: NodeRef
    phase: str


class CloudProvider(Protocol):
    """Provider contract for cost-aware node scaling."""

    def list_node_types(self, region: str) -> list[NodeType]:
        """List available node types for a region."""

    def create_node(self, spec: NodeSpec) -> NodeRef:
        """Create a node from the given specification."""

    def delete_node(self, ref: NodeRef) -> None:
        """Delete a previously created node."""

    def get_node(self, ref: NodeRef) -> NodeStatus:
        """Get current lifecycle status for a node."""

    def list_nodes(self, region: str | None = None) -> list[NodeStatus]:
        """List nodes optionally filtered by region."""
