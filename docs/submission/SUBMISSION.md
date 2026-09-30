# Sawt al-Tameen: submission form copy

Event: [AssemblyAI Voice Agent Hackathon](https://lablab.ai/ai-hackathons/assemblyai-voice-agent-hackathon)

Deadline verified on the rendered event page: **30 September 2026, 15:00 UTC / 20:30 IST**.

## Title

Sawt al-Tameen: Insurance Voice Agent

## Short description

An AssemblyAI voice agent for UAE insurance pre-authorisation. It verifies callers, checks a synthetic benefit catalogue, requests missing documents and prepares an auditable case. A qualified human makes the final decision.

## Long description

Sawt al-Tameen, the voice of insurance, is an English-language pre-authorisation intake assistant for a fictional UAE health insurer. A clinic can describe a treatment request by voice, confirm member details and receive clear guidance about the next step. The agent prepares a case for a qualified human reviewer.

AssemblyAI's Voice Agent API provides the hosted conversation, speech recognition, managed conversational model, speech output and turn handling. Our FastAPI backend bridges browser audio at 24 kHz PCM and executes three JSON-Schema tools: verify_caller, check_coverage_rule and log_transcript. API keys and tool credentials stay on the server. A separate Twilio bridge supports 8 kHz PCMU phone audio, although a live phone number is not configured for this submission.

The agent works against a synthetic benefit catalogue containing four policy tiers, 50 procedures, 20 providers, 20 members and eight escalation rules. A deterministic rules engine checks limits, network eligibility, waiting periods and document requirements. Recommendations cite the catalogue sections that produced them. Supporting documents can be uploaded through an authenticated document desk, followed by a recheck of the same case.

Human decision authority is a software boundary. The agent has no approval or denial tool. A reviewer-only API and case state machine enforce sign-off, and a voice-linked case requires its completed transcript before a reviewer can decide. The system preserves both the advisory recommendation and the human decision in its audit trail.

The intended users are insurer intake teams and provider authorisation desks. The business hypothesis is that guided collection and explicit missing-document requirements can reduce incomplete handoffs and repeated administrative calls. A paid pilot would measure intake handling time, rework, document completeness, identifier accuracy and cost per completed case. We have not measured customer savings or clinical outcomes.

Validation on 30 September includes 393 passing Python tests, 46 passing JavaScript audio/browser tests and 26 passing public deployment checks. Two actual AssemblyAI conversations with a synthetic spoken caller completed the document-request/upload/recheck flow and failed-verification/callback flow. The first remained pending human review. These results establish the exercised prototype workflows, rather than production readiness. Full procedure readback remains inconsistent, and physical speaker echo and varied human accents require further evaluation.

All people, policies, providers and tariffs in the demo are fictional. It supports English and AED amounts. The public browser demo needs no account. Document uploads require an operator access key. The MIT-licensed repository includes reproducible setup, tests and an acceptance report.

## Technology and category tags

Choose the closest tags available in the form:

- AssemblyAI / Voice Agent API
- Voice AI / Conversational AI / Speech-to-Text
- Python / FastAPI / WebSockets
- Insurance / Healthcare administration / Business automation
- SQLAlchemy / SQLite

Twilio is an implemented optional integration. Ollama, faster-whisper and Piper belong to the optional offline mode. Do not select an OpenAI or custom LLM tag for the hosted demo: it uses AssemblyAI's managed conversational model.

## Hosting and repository fields

- Intended repository: https://github.com/adib-cyber007/sawt-al-tameen (currently returns 404 to unauthenticated visitors; publish and verify public access before submitting)
- Application URL: https://dividable-fretted-aroma.ngrok-free.dev/voice
- Demo guide: https://dividable-fretted-aroma.ngrok-free.dev/voice/assets/judges.html
- Application platform: Python FastAPI, hosted on Windows through a static ngrok HTTPS tunnel
- Cover upload: `cover.png`
- Slide presentation: `pitch-deck.pdf` (editable source: `pitch-deck.pptx`)
- Video presentation: `presentation.mp4`

Upload the video through the lablab form if supported. If the form requires a hosted video URL, upload the MP4 to an accessible video host and paste that URL. A local file path is not a public video URL.

## Sources and evidence

- Event requirements and rubric: https://lablab.ai/ai-hackathons/assemblyai-voice-agent-hackathon
- Platform guide: https://lablab.ai/guide
- Official submission guidance: https://lablab.ai/delivering-your-hackathon-solution
- Published general guide: https://github.com/lablab-ai/community-content/blob/main/blog/en/hackathon-guidelines.mdx
- Project evidence: `docs/CONVERSATION_ACCEPTANCE.md`, test output and `readiness-report.json`

The general guide lists a title of at most 50 characters, a summary of at most 255 characters, at least 100 words of long description, a recommended 16:9 cover and a video within five minutes and under 300 MB. Follow the actual form if it imposes different constraints.

## Final operator checklist

- Confirm every team member enrolled and joined the lablab team, including solo entrants.
- Confirm original work and accurately disclose reused components. The available Git history starts on 16 September 2026 and records an earlier voice-provider integration before the AssemblyAI migration. Describe the submitted AssemblyAI implementation accurately.
- Publish the prepared repository commit and verify its public main branch includes this package and LICENSE.
- Keep the demo computer connected to power and the internet throughout judging. Its static hostname depends on the running backend and tunnel.
- Open the demo in a fresh browser. The free ngrok domain may display an initial notice.
- Upload the cover, PDF and video, or supply the required public video link.
- Paste the fields above, review the completed form and submit before the deadline.
- Save the final submission URL or confirmation. A prepared package or saved draft is not a submitted entry.
