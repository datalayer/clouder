"""Vendored CSI specification and generated Python stubs.

``csi.proto`` is the Container Storage Interface spec, tag ``v1.11.0``
(https://github.com/container-storage-interface/spec/blob/v1.11.0/csi.proto),
copied verbatim. The ``csi_pb2*.py`` files are generated from it with
``grpcio-tools`` and committed so that the driver image and the tests need no
compiler. Regenerate from the Clouder repository root with::

    python -m grpc_tools.protoc -I. \
        -I"$(python -c 'import grpc_tools, os; print(os.path.join(os.path.dirname(grpc_tools.__file__), "_proto"))')" \
        --python_out=. --grpc_python_out=. --pyi_out=. \
        clouder/csi/proto/csi.proto

The generated modules require ``grpcio>=1.67`` and ``protobuf>=5.27``; both
come with the ``clouder[csi]`` extra.
"""
