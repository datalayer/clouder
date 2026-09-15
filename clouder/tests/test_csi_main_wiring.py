# Copyright (c) 2023-2026 Datalayer, Inc.
# Datalayer License

"""Every flag the entrypoint reads is one the parser produces.

This is not a subtle property, and it broke anyway: renaming the flags from
`--gateway-*` to `--node-mount-gateway-*` moved every destination argparse
derives from them, and the `args.gateway_*` reads in `main()` kept parsing,
kept passing every other test, and would have raised `AttributeError` on the
first node that started with the gateway switched on. Nothing else covers it,
because nothing else calls `main()` with the gateway enabled.
"""

import ast
import pathlib

from ..csi.__main__ import build_parser


SOURCE = pathlib.Path(__file__).parent.parent / "csi" / "__main__.py"


def test_every_flag_main_reads_is_one_the_parser_defines():
    known = set(vars(build_parser().parse_args(["--node-id", "n"])))

    read = {
        node.attr
        for node in ast.walk(ast.parse(SOURCE.read_text()))
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "args"
    }

    assert read <= known, f"main() reads flags the parser does not define: {sorted(read - known)}"


def test_the_gateway_flags_are_all_named_for_the_gateway():
    flags = {
        action.option_strings[0]
        for action in build_parser()._actions
        if action.option_strings and "gateway" in action.dest
    }

    # One name for the thing, everywhere: a `--gateway-buckets` left over from
    # the rename is a flag that reads like a different feature.
    assert all(flag.startswith("--node-mount-gateway") for flag in flags), sorted(flags)
