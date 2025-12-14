# Re-export from linuxcnc_pb for backwards compatibility
# The actual generated code lives in packages/python/linuxcnc_pb/
from linuxcnc_pb.linuxcnc_pb2 import *
from linuxcnc_pb.linuxcnc_pb2_grpc import *
from linuxcnc_pb.hal_pb2 import *
from linuxcnc_pb.hal_pb2_grpc import *

# Also expose the modules directly for `from linuxcnc_grpc._generated import linuxcnc_pb2` style imports
from linuxcnc_pb import linuxcnc_pb2
from linuxcnc_pb import linuxcnc_pb2_grpc
from linuxcnc_pb import hal_pb2
from linuxcnc_pb import hal_pb2_grpc
