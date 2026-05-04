from typing import Any, Dict, List


def _jupyter_server_extension_points() -> List[Dict[str, Any]]:
    from .serverapplication import ClouderServerApp
    return [{
        "module": "clouder",
        "app": ClouderServerApp,
    }]
