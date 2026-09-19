from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from preauth.agent_tools.toolbox import AgentToolbox
from preauth.agent_tools.voice_gateway import ASSEMBLYAI_AGENT_ACTOR, VoiceToolGateway
from preauth.api.errors import install_error_handlers
from preauth.api.middleware import RequestContextMiddleware
from preauth.api.routes import agent, assemblyai, cases, review, twilio, voice
from preauth.application.services import ApplicationServices
from preauth.application.twilio_inbound_service import TwilioInboundService
from preauth.infrastructure.observability import install_log_context
from preauth.infrastructure.settings import Settings

DESCRIPTION = """
Backend for provider pre-authorisation requests: case intake, validation, rule evaluation, advisory
recommendations, human review, and an immutable audit trail.

**The system never issues final authorisations or denials.** Only an authorised human reviewer can move a case
to `APPROVED` or `DENIED`.

**Authentication assumption:** requests arrive through a gateway that authenticates the caller and sets
`X-Actor-Type`, `X-Actor-Id`, and (for reviewers) `X-Actor-Roles`. When the service is exposed on a public URL
without such a gateway, set `PREAUTH_GATEWAY_SECRET`; every `/api/v1` route except the voice channel then also
requires a matching `X-Gateway-Secret` header. Voice-channel routes authenticate separately (bearer token for
tool calls, HMAC signature for the post-call webhook).

Every response carries `X-Request-ID`. Errors use a uniform envelope: `{"error": {"code", "message", "details",
"request_id", "case_id"}}`.
"""


def create_app(
    services: ApplicationServices,
    settings: Settings | None = None,
    local_runtime: Any | None = None,
) -> FastAPI:
    """Build the HTTP application.

    ``local_runtime`` is a ``preauth.local.runtime.LocalRuntime`` when this process serves the local channel. It
    is the single place local mode changes anything: with it, the local routes and browser UI are mounted; without
    it, this is exactly the ElevenLabs-facing service. Nothing below the API layer is aware of the difference.
    """
    install_log_context()
    app = FastAPI(title="Pre-Authorisation Case Service", version="0.1.0", description=DESCRIPTION)
    app.state.services = services
    app.state.settings = settings or Settings()
    app.state.toolbox = AgentToolbox(services)
    app.state.voice_gateway = VoiceToolGateway(services, app.state.toolbox)
    app.state.assemblyai_voice_gateway = VoiceToolGateway(
        services, app.state.toolbox, ASSEMBLYAI_AGENT_ACTOR
    )
    app.add_middleware(RequestContextMiddleware)
    install_error_handlers(app)
    app.include_router(cases.router)
    app.include_router(review.router)
    app.include_router(agent.router)
    app.include_router(voice.router)
    app.include_router(assemblyai.router)
    app.state.twilio_inbound = TwilioInboundService.from_settings(app.state.settings)
    app.include_router(twilio.router)
    if app.state.settings.assemblyai_enabled:
        hosted_web = Path(__file__).resolve().parent.parent / "hosted_web"
        app.mount("/voice/assets", StaticFiles(directory=hosted_web), name="hosted-voice-assets")

        @app.get("/voice", tags=["Voice channel (AssemblyAI)"], summary="Hosted voice console", include_in_schema=False)
        def hosted_voice_console() -> FileResponse:
            return FileResponse(hosted_web / "index.html")

    if local_runtime is not None:
        from preauth.api.routes import local as local_routes

        app.state.local_runtime = local_runtime
        app.include_router(local_routes.router)
        web = Path(__file__).resolve().parent.parent / "local" / "web"
        app.mount("/local/assets", StaticFiles(directory=web), name="local-assets")

        @app.get("/local", tags=["Voice channel (local)"], summary="Local voice console", include_in_schema=False)
        def local_console() -> FileResponse:
            return FileResponse(web / "index.html")

    @app.get("/health", tags=["Operations"], summary="Liveness check")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
