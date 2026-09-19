"""Backfill completed AssemblyAI Voice Agent sessions whose webhook delivery was missed.

This uses the same idempotent post-call ingestion path as the signed webhook. It is safe to rerun: sessions that
already have a call record are reported as already recorded, while sessions whose timeline artifact is still being
prepared are left pending for a later run.

Usage:
    python scripts/assemblyai_reconcile.py
    python scripts/assemblyai_reconcile.py --agent-id agent_123 --limit 500
"""

import argparse
import sys

from preauth.application.assemblyai_post_call import (
    AssemblyAIArtifactPendingError,
    AssemblyAIPostCallService,
    AssemblyAIProviderUnavailableError,
)
from preauth.application.services import build_services
from preauth.infrastructure.assemblyai_client import AssemblyAIClient, AssemblyAIError
from preauth.infrastructure.db.session import build_engine, build_session_factory
from preauth.infrastructure.settings import Settings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--agent-id", help="only reconcile sessions for one AssemblyAI agent")
    parser.add_argument("--limit", type=int, default=1000, help="maximum sessions to inspect (default: 1000)")
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit must be at least 1")

    settings = Settings.from_env()
    if not settings.assemblyai_api_key:
        print("ASSEMBLYAI_API_KEY is required", file=sys.stderr)
        return 2

    engine = build_engine(settings.database_url)
    services = build_services(build_session_factory(engine))
    client = AssemblyAIClient(settings.assemblyai_api_key, api_base=settings.assemblyai_api_base)
    post_call = AssemblyAIPostCallService(settings, services.voice, client=client)

    inspected = recorded = already = pending = errors = 0
    cursor: str | None = None
    try:
        while inspected < args.limit:
            page_size = min(200, args.limit - inspected)
            page = client.list_sessions(
                status="completed", agent_id=args.agent_id, limit=page_size, cursor=cursor
            )
            sessions = page.get("sessions") or page.get("data") or []
            if not isinstance(sessions, list):
                raise AssemblyAIError("AssemblyAI session listing had an unexpected JSON shape")
            for session in sessions:
                if inspected >= args.limit:
                    break
                inspected += 1
                if not isinstance(session, dict):
                    errors += 1
                    continue
                session_id = session.get("id") or session.get("session_id")
                if not isinstance(session_id, str) or not session_id:
                    errors += 1
                    continue
                try:
                    # Listing responses are summaries and may omit artifacts. Fetch the authoritative detail.
                    outcome = post_call.ingest_session(session_id)
                except AssemblyAIArtifactPendingError:
                    pending += 1
                    print(f"pending  {session_id}")
                except AssemblyAIProviderUnavailableError as exc:
                    errors += 1
                    print(f"error    {session_id}: {exc}", file=sys.stderr)
                else:
                    if outcome.detail == "Already recorded":
                        already += 1
                        print(f"exists   {session_id}")
                    else:
                        recorded += 1
                        print(f"recorded {session_id}")
            cursor_value = page.get("next_cursor") or page.get("cursor")
            cursor = cursor_value if isinstance(cursor_value, str) and cursor_value else None
            if not sessions or not cursor:
                break
    except AssemblyAIError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        engine.dispose()

    print(
        f"inspected={inspected} recorded={recorded} already={already} pending={pending} errors={errors}"
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
