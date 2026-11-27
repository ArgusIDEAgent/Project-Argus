"""Read current isolation capability. Never starts a test container."""
import json
from .broker import isolation_capabilities
print(json.dumps(isolation_capabilities(), indent=2))
