import subprocess

from pathlib import Path


HERE = Path(__file__).parent


def run_command(args):
  """Run a command."""

  args[2] = args[2] + ".sh"

  cmd = ["sh", str(HERE / ".." / "sbin" / "clouder.sh")]
  cmd.extend(args[2:])

  subprocess.run(cmd)
