import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="avatar-api-test-")
os.environ.update({
    "DATABASE_URL": f"sqlite+aiosqlite:///{_tmp}/test.db",
    "STORAGE_BACKEND": "local",
    "LOCAL_STORAGE_DIR": f"{_tmp}/storage",
    "API_KEYS": "test-key",
    "INTERNAL_API_TOKEN": "internal-secret",
    "SIMLI_API_KEY": "",
    "LANGFUSE_PUBLIC_KEY": "",  # no telemetry export from tests
    "OTEL_EXPORTER_OTLP_ENDPOINT": "",
    "DAILY_SPEND_CAP_USD": "1.0",
})
