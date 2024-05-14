from typing import Any, Dict, List

from .serverapplication import ClouderServerApp


def _jupyter_server_extension_points() -> List[Dict[str, Any]]:
    return [{
        "module": "clouder",
        "app": ClouderServerApp,
    }]
