"""
A command helper that says it is ready and then never reads a request, as
a helper stopped or stuck would. Used to test the server's write deadline.
"""

import json
import os
import sys
import time

sys.stdout.write(json.dumps({"ready": True, "pid": os.getpid(), "serial": 0}) + "\n")
sys.stdout.flush()
while True:
    time.sleep(60)
