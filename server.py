"""Jobby — Your Job Applications Tracking Buddy.

The app lives in the jobby package; this module is the command line entry
point (`python3 server.py`), and the name the tests and launchers import.
"""

import install_tectonic  # noqa: F401  (the engine installer reaches it here)
from jobby import (  # noqa: F401  (tests patch these modules in place)
    applications,
    auth,
    config,
    db,
    documents,
    http_server,
    resumes,
)
from jobby.applications import clean_create, create_application  # noqa: F401  (re-exported for tests and callers)
from jobby.config import PIN_FREE_ATTEMPTS  # noqa: F401  (re-exported for tests and callers)
from jobby.db import MIGRATIONS, SCHEMA_VERSION, backup_database, connect, init_db, prune_backups, sent_counts, table_columns  # noqa: F401  (re-exported for tests and callers)
from jobby.http_server import DashboardClients, Handler, watch_idle  # noqa: F401  (re-exported for tests and callers)
from jobby.resumes import TECTONIC_DIR, latex_engine  # noqa: F401  (re-exported for tests and callers)
from jobby.http_server import main


if __name__ == "__main__":
    main()
