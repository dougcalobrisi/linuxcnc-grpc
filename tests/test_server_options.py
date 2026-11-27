"""Server-level gRPC options."""

import sys
from unittest.mock import MagicMock, patch


def test_receive_limit_leaves_headroom_above_upload_cap():
    """A near-limit upload must reach UploadFile's size check, not die in the transport."""
    with patch.dict(sys.modules, {"linuxcnc": MagicMock(), "hal": MagicMock()}):
        from linuxcnc_grpc import server
        from linuxcnc_grpc.linuxcnc_service import MAX_UPLOAD_SIZE

        # Build the server for real, capturing what is handed to grpc.server().
        with patch.object(server.grpc, "server") as grpc_server_factory, \
                patch.object(server, "LinuxCNCServiceServicer"), \
                patch.object(server, "HalServiceServicer"):
            server.create_server(host="127.0.0.1", port=0)

    grpc_server_factory.assert_called_once()
    options_passed_to_grpc = dict(grpc_server_factory.call_args.kwargs["options"])
    receive_limit_bytes = options_passed_to_grpc["grpc.max_receive_message_length"]

    assert receive_limit_bytes == server.MAX_RECEIVE_MESSAGE_BYTES
    assert receive_limit_bytes >= MAX_UPLOAD_SIZE + 64 * 1024
