from datetime import datetime

import pydantic


class SyncState(pydantic.BaseModel):
    """Tracks the last poll timestamps for incremental synchronization."""

    event_last_poll_at: datetime | None = None
    patrol_last_poll_at: datetime | None = None
