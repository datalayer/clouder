"""Azure cloud provider configuration."""

import yaml
from pathlib import Path

from ...util.utils import CLOUDER_CLOUDS_FOLDER


AZURE_CONFIG_FOLDER = CLOUDER_CLOUDS_FOLDER / "azure"
AZURE_CONFIG_FILE = AZURE_CONFIG_FOLDER / "azure.yaml"


def load_azure_config() -> dict:
    """Load Azure configuration from file.

    Returns:
        dict with keys: subscription_id, tenant_id, client_id, client_secret
    """
    if not AZURE_CONFIG_FILE.is_file():
        return {}
    with open(AZURE_CONFIG_FILE, "r") as f:
        return yaml.safe_load(f) or {}


def save_azure_config(config: dict):
    """Save Azure configuration to file."""
    AZURE_CONFIG_FOLDER.mkdir(parents=True, exist_ok=True)
    with open(AZURE_CONFIG_FILE, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)
    AZURE_CONFIG_FILE.chmod(0o600)


def get_azure_subscription_id() -> str:
    """Get the Azure subscription ID from config."""
    config = load_azure_config()
    return config.get("subscription_id", "")


def get_azure_credential():
    """Get Azure credential using the configured authentication method.

    Supports:
    1. Service Principal (client_id + client_secret + tenant_id)
    2. Default Azure credential chain (az login, managed identity, env vars, etc.)
    """
    from azure.identity import DefaultAzureCredential, ClientSecretCredential

    config = load_azure_config()
    tenant_id = config.get("tenant_id", "")
    client_id = config.get("client_id", "")
    client_secret = config.get("client_secret", "")

    if tenant_id and client_id and client_secret:
        return ClientSecretCredential(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
        )
    # Fall back to DefaultAzureCredential (az login, env vars, managed identity, etc.)
    return DefaultAzureCredential()
