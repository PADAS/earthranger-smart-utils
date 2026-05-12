# Compatibility shim: smartconnect-client v1.11.0 imports from
# cdip_connector.core.schemas, which was renamed to gundi_core.schemas.
from gundi_core.schemas import *  # noqa: F401,F403
