import atexit
import os
import shutil
import tempfile
from pathlib import Path

_BOOTSTRAP_DIR = Path(tempfile.mkdtemp(prefix="supportops-tests-"))
atexit.register(shutil.rmtree, _BOOTSTRAP_DIR, ignore_errors=True)

# Keep module-level app creation isolated from a developer's local .env file.
os.environ.update(
    {
        "DATABASE_PATH": str(_BOOTSTRAP_DIR / "supportops.db"),
        "QDRANT_PATH": str(_BOOTSTRAP_DIR / "qdrant"),
        "QDRANT_URL": "",
        "ANSWER_PROVIDER": "context",
        "EMBEDDING_BACKEND": "hash",
        "APP_ENVIRONMENT": "development",
        "ENABLE_API_DOCS": "true",
        "API_KEY": "",
        "WEBHOOK_SECRET": "",
    }
)
