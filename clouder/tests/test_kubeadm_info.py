from ..cli.kubeadm.info import _cluster_from_metadata


def test_cluster_inventory_uses_complete_persisted_metadata():
    metadata = {
        "cloud": "aws",
        "master": {"name": "prod1-master-51f1", "ip": "198.51.100.10"},
        "workers": [
            {"name": "prod1-node-1-6d79", "ip": "198.51.100.11"},
            {"name": "prod1-node-2-84eb", "ip": "198.51.100.12"},
        ],
    }

    cluster = _cluster_from_metadata(metadata, "575108930674")

    assert cluster == {
        "cloud": "aws",
        "master": {"name": "prod1-master-51f1", "ip": "198.51.100.10"},
        "workers": [
            {"name": "prod1-node-1-6d79", "ip": "198.51.100.11"},
            {"name": "prod1-node-2-84eb", "ip": "198.51.100.12"},
        ],
        "context_id": "575108930674",
    }


def test_cluster_inventory_falls_back_when_metadata_is_incomplete():
    metadata = {
        "cloud": "aws",
        "master": {"name": "prod1-master-51f1"},
        "workers": [],
    }

    assert _cluster_from_metadata(metadata, "575108930674") is None
