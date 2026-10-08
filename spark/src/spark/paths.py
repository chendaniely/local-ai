"""Default locations on brightroar; each can be overridden by an environment variable."""

import os
from pathlib import Path

REGISTRY = Path(os.environ.get("SPARK_REGISTRY", "/opt/local-ai/etc/models.yaml"))
KEYS = Path(os.environ.get("SPARK_KEYS", "/etc/local-ai/keys.yaml"))  # the private key list, never in the repo
VALUES = Path(os.environ.get("SPARK_VALUES", "/etc/local-ai/values.env"))  # the private values file, never in the repo
STATE = Path(os.environ.get("SPARK_STATE", "/var/lib/local-ai/brake"))
GATE_STATE = Path(os.environ.get("SPARK_GATE_STATE", "/var/lib/local-ai/gate"))  # the gate's state, across restarts
LAUNCH = Path(os.environ.get("SPARK_LAUNCH", "/var/lib/local-ai/launch"))  # admission tickets, and launch's records
WHISPER_TMP = Path(os.environ.get("SPARK_WHISPER_TMP", "/var/lib/local-ai/whisper-tmp"))  # whisper's --tmp-dir
# The gate's two sockets, which systemd holds: the status socket for group spark-users, the control socket for
# spark-admin (gateproto.SOCKET_GROUPS).
GATE_STATUS_SOCKET = Path(os.environ.get("SPARK_GATE_STATUS", "/run/local-ai/gate-status.sock"))
GATE_CONTROL_SOCKET = Path(os.environ.get("SPARK_GATE_CONTROL", "/run/local-ai/gate-control.sock"))
# From Phase 2a's cutover the front holds 9100, where clients reach the models, and llama-swap moves behind it to 900.
FRONT_URL = os.environ.get("SPARK_FRONT_URL", "http://127.0.0.1:9100")
LLAMASWAP_URL = os.environ.get("SPARK_LLAMASWAP_URL", "http://127.0.0.1:900")
