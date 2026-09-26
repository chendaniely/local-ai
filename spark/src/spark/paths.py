"""Default locations on brightroar; each can be overridden by an environment variable."""

import os
from pathlib import Path

REGISTRY = Path(os.environ.get("SPARK_REGISTRY", "/opt/local-ai/etc/models.yaml"))
STATE = Path(os.environ.get("SPARK_STATE", "/var/lib/local-ai/brake"))
LLAMASWAP_URL = os.environ.get("SPARK_LLAMASWAP_URL", "http://127.0.0.1:9100")
