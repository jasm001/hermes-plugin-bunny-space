# Bunny Space platform adapter (deferred import). The adapter module carries the
# heavy imports; here we only re-export the plugin entry point.
from .adapter import register

__all__ = ["register"]
