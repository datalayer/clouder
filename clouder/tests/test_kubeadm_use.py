from typer import Typer
from typer.testing import CliRunner

from ..cli.kubeadm import use as use_cli


runner = CliRunner()


def test_use_persists_selected_cluster_and_prints_export(monkeypatch, tmp_path):
    kubeconfig = tmp_path / "prod1" / "kubeconfig"
    kubeconfig.parent.mkdir()
    kubeconfig.touch()
    selected = []

    monkeypatch.setattr(use_cli, "kubeadm_kubeconfig_path", lambda name: kubeconfig)
    monkeypatch.setattr(use_cli, "set_default_kubeadm_cluster", selected.append)

    app = Typer()
    use_cli.register(app)

    result = runner.invoke(app, ["prod1", "--print-export"])

    assert result.exit_code == 0
    assert result.stdout == f'export KUBECONFIG="{kubeconfig}"\n'
    assert selected == ["prod1"]
