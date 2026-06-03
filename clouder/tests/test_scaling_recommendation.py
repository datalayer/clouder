from clouder.operator.scaling_recommendation import (
    RecommendationNode,
    RecommendationPod,
    recommend_scaling_action,
)


def test_recommend_scale_up_for_unschedulable_pressure() -> None:
    recommendation = recommend_scaling_action(
        nodes=[RecommendationNode(name="n1")],
        pods=[RecommendationPod(name="p1", phase="Pending", unschedulable=True)],
        min_nodes=0,
        max_nodes=10,
        scale_up_pressure_threshold=1,
        scale_down_max_step=1,
    )

    assert recommendation.action == "scale_up"
    assert recommendation.target_delta_nodes == 1


def test_recommend_scale_down_for_idle_node_ignoring_daemonset_and_statefulset() -> None:
    recommendation = recommend_scaling_action(
        nodes=[
            RecommendationNode(name="worker-1"),
            RecommendationNode(name="worker-2"),
        ],
        pods=[
            RecommendationPod(
                name="ds-pod",
                phase="Running",
                node_name="worker-2",
                owner_kind="DaemonSet",
            ),
            RecommendationPod(
                name="sts-pod",
                phase="Running",
                node_name="worker-2",
                owner_kind="StatefulSet",
            ),
            RecommendationPod(
                name="workload-pod",
                phase="Running",
                node_name="worker-1",
                owner_kind="",
            ),
        ],
        min_nodes=1,
        max_nodes=10,
        scale_up_pressure_threshold=1,
        scale_down_max_step=1,
    )

    assert recommendation.action == "scale_down"
    assert recommendation.target_delta_nodes == 1
