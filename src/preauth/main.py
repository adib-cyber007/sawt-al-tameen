"""Process entry point: ``uvicorn preauth.main:app``.

Runs the AssemblyAI-facing hosted service by default. With ``PREAUTH_RUNTIME_MODE=local`` it additionally mounts
the local voice channel and its browser console. Both modes serve the same application, rules and review layers.
"""

from preauth.api.app import create_app
from preauth.application.services import build_services
from preauth.infrastructure.db.session import build_engine, build_session_factory
from preauth.infrastructure.observability import configure_logging
from preauth.infrastructure.settings import Settings

settings = Settings.from_env()
configure_logging(settings.log_level)
services = build_services(build_session_factory(build_engine(settings.database_url)))

local_runtime = None
if settings.local_mode:
    from preauth.local.runtime import LocalRuntime

    # Built eagerly, but the model, recogniser and voice load lazily on first use, so a missing model does not
    # stop the service from starting and reporting what is missing through /api/v1/local/diagnostics.
    local_runtime = LocalRuntime.build(services)

app = create_app(services, settings, local_runtime)
