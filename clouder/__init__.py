from typing import Any, Dict, List

from .serverapplication import ClouderServerApp


__version__ = '0.0.6'


def _jupyter_server_extension_points() -> List[Dict[str, Any]]:
    return [{
        "module": "clouder",
        "app": ClouderServerApp,
    }]


def _jupyter_labextension_paths() -> List[Dict[str, str]]:
    return [{
        "src": "labextension",
        "dest": "@datalayer/clouder"
    }]
