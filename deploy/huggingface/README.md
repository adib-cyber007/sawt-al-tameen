---
title: Sawt Al Tameen
emoji: 📞
colorFrom: blue
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
---

# Sawt Al Tameen — pre-authorisation backend

Backend for the Sawt Assurance provider pre-authorisation voice line (synthetic UAE catalogue). See the
[source repository](https://github.com/haroon12h08/sawt-al-tameen) for documentation.

**Copy this file to the root of your Hugging Face Space repository as `README.md`.** Hugging Face requires the
front matter above to build the Space as a Docker app on port 7860.

Set these as Space secrets (Settings → Variables and secrets):

| Secret | Purpose |
|---|---|
| `PREAUTH_DATABASE_URL` | Hosted Postgres URL (e.g. Neon). Without it the database is wiped on restart. |
| `VOICE_PROVIDER` | Set to `assemblyai` (the default). |
| `ASSEMBLYAI_API_KEY` | AssemblyAI Voice Agent REST, WebSocket and Sessions API credential. |
| `PREAUTH_ASSEMBLYAI_BROWSER_AGENT_ID` | Stored browser-agent id created by `scripts/assemblyai_setup.py`. |
| `PREAUTH_ASSEMBLYAI_PHONE_AGENT_ID` | Stored PCMU phone-agent id created by `scripts/assemblyai_setup.py`. |
| `PREAUTH_ASSEMBLYAI_WEBHOOK_SECRET` | Verifies completed-session deliveries from AssemblyAI. |
| `PREAUTH_ASSEMBLYAI_MEDIA_SECRET` | Signs short-lived Twilio media-stream URLs. |
| `PREAUTH_PUBLIC_BASE_URL` | Stable public HTTPS URL of this Space. |
| `PREAUTH_VOICE_TOOL_TOKEN` | Token for the provider-neutral deployment diagnostic endpoint. |
| `PREAUTH_GATEWAY_SECRET` | Required on staff and reviewer APIs |
| `PREAUTH_SEED_SCENARIOS` | Optional: set to `1` to create the five demo cases on first start |

For Twilio calls, also configure `TWILIO_AUTH_TOKEN` and point the number's incoming-call webhook at
`<PREAUTH_PUBLIC_BASE_URL>/api/v1/voice/twilio/inbound`. The Space must be **public** so browsers, Twilio and
AssemblyAI can reach the media and completed-session endpoints.

On first start the container runs the migration and loads the benefit catalogue from `knowledge_base/`, which is
the authoritative data queried by the agent's constrained tools.
