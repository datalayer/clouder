"""Clouder scaling recommendation helpers.

This module exposes a small, reusable API that can be consumed by external
controllers (such as datalayer-operator) to decide whether to scale up,
scale down, or do nothing based on:
- pending unscheduled pods pressure
- removable worker nodes that only host ignored pod owners
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RecommendationNode:
    """Minimal node snapshot used for recommendation."""

    name: str
    control_plane: bool = False
    ready: bool = True
    schedulable: bool = True


@dataclass(frozen=True)
class RecommendationPod:
    """Minimal pod snapshot used for recommendation."""

    name: str
    phase: str
    node_name: str | None = None
    unschedulable: bool = False
    owner_kind: str = ""


@dataclass(frozen=True)
class ScalingRecommendation:
    """Recommended scaling action and context."""

    action: str
    target_delta_nodes: int = 0
    reason: str = ""
    metadata: dict[str, str] = field(default_factory=dict)


def recommend_scaling_action(
    *,
    nodes: list[RecommendationNode],
    pods: list[RecommendationPod],
    min_nodes: int,
    max_nodes: int,
    scale_up_pressure_threshold: int = 1,
    scale_down_max_step: int = 1,
    ignored_owner_kinds: tuple[str, ...] = ("DaemonSet", "StatefulSet"),
) -> ScalingRecommendation:
    """Return a scaling recommendation for the provided snapshot.

    Rules:
    - Scale up when pending unschedulable pods pressure is above threshold.
    - Scale down when pressure is low and there are removable worker nodes with
      no workload pods other than ignored owner kinds.
    - Otherwise no-op.
    """

    worker_nodes = [node for node in nodes if not node.control_plane]
    worker_count = len(worker_nodes)

    pressure = sum(1 for pod in pods if pod.unschedulable)
    if pressure >= max(0, int(scale_up_pressure_threshold)):
        target_delta = min(
            max(1, pressure),
            max(0, int(max_nodes) - worker_count),
        )
        if target_delta > 0:
            return ScalingRecommendation(
                action="scale_up",
                target_delta_nodes=target_delta,
                reason="clouder_api: pending unschedulable pods",
                metadata={
                    "pressure_unschedulable": str(pressure),
                    "worker_nodes": str(worker_count),
                },
            )
        return ScalingRecommendation(
            action="no_op",
            reason="clouder_api: pressure detected but max_nodes reached",
            metadata={
                "pressure_unschedulable": str(pressure),
                "worker_nodes": str(worker_count),
            },
        )

    if worker_count <= int(min_nodes):
        return ScalingRecommendation(
            action="no_op",
            reason="clouder_api: at or below min_nodes",
            metadata={"worker_nodes": str(worker_count)},
        )

    candidate_names = {
        node.name
        for node in worker_nodes
        if node.ready and node.schedulable
    }
    workload_counts = {name: 0 for name in candidate_names}
    ignored_owners = set(ignored_owner_kinds)

    for pod in pods:
        if str(pod.phase or "").lower() != "running":
            continue
        node_name = str(pod.node_name or "").strip()
        if node_name not in workload_counts:
            continue
        owner_kind = str(pod.owner_kind or "").strip()
        if owner_kind in ignored_owners:
            continue
        workload_counts[node_name] += 1

    removable_nodes = [name for (name, count) in workload_counts.items() if count == 0]
    removable_budget = max(0, worker_count - int(min_nodes))
    removable_count = min(
        len(removable_nodes),
        max(1, int(scale_down_max_step)),
        removable_budget,
    )

    if removable_count > 0:
        return ScalingRecommendation(
            action="scale_down",
            target_delta_nodes=removable_count,
            reason="clouder_api: removable worker nodes without workload pods",
            metadata={
                "worker_nodes": str(worker_count),
                "removable_nodes": str(len(removable_nodes)),
                "ignored_owner_kinds": ",".join(sorted(ignored_owners)),
            },
        )

    return ScalingRecommendation(
        action="no_op",
        reason="clouder_api: no removable nodes and no pending pressure",
        metadata={
            "worker_nodes": str(worker_count),
            "removable_nodes": "0",
        },
    )
