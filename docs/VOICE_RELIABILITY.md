# Voice reliability improvements 1–3

Implemented from the first three recommendations in `CORE_RESEARCH_2026-09-29.md`.

## Twilio admission

TwiML carries the 90-second, CallSid-bound token in a nested `<Parameter name="token">`.
The stream URL has no query string, as required by the [Twilio Stream contract](https://www.twilio.com/docs/voice/twiml/stream#url).
The WebSocket upgrade checks `X-Twilio-Signature` against the configured public WSS URL, including its path prefix.
It also accepts Twilio's documented trailing-slash signing variant; it does not trust forwarded hosts.
See [Twilio signature validation](https://www.twilio.com/docs/usage/security).

After accepting the socket, admission has five seconds and at most two text messages: an optional
`connected` followed by `start`. Each message is limited to 4 KiB at the application boundary. The server checks
the custom token, CallSid, matching StreamSids, and mono 8 kHz mu-law media format. A durable unique admission
record prevents replay across workers or restarts. Only then can the application open an AssemblyAI session.
The admission table stores the token's SHA-256 hash, never the token. One CallSid may be admitted once; a
rejected/replayed stream requires a new call. Provider-side reconnects retain the already admitted Twilio stream.
The ASGI server/proxy should also impose transport-level message and connection limits; the application size
check happens after the server receives a complete WebSocket message.

## Tool execution and reply delivery

The provider receive loop handles each event without awaiting tool completion. A separate dispatcher sends
results only at a completed reply boundary for the current turn generation, including a tool call arriving just after that boundary.
This retains the [AssemblyAI events contract](https://www.assemblyai.com/docs/voice-agents/voice-agent-api/events-reference)
and the asynchronous pattern in its [Twilio example](https://github.com/AssemblyAI-Solutions/voice-agent-api-twilio-example).

Speech start and a new reply pause dispatch without discarding work. Only an explicitly interrupted reply invalidates pending delivery. Queued work from that generation is skipped;
already-started synchronous work can finish. A 15-second response deadline returns `TOOL_TIMEOUT` with an
explicit unknown-outcome message. It cannot kill a Python thread or undo a transaction. Later business tools
stay ordered behind the actual execution, including after timeout. At most 32 executions/results are pending
per coordinator. Disconnect disables dispatch; resume requires the same session identity and a new safe boundary.
Final shutdown cancels dispatch/monitors while retaining started work long enough to record its outcome.

## Durable retries and atomic effects

`voice_tool_executions` assigns an application `request_id`, hashes normalized arguments, and stores the response.
`voice_provider_calls` binds each provider `call_id` to that execution within an actor and conversation.
Reusing a call id with different data returns `TOOL_REQUEST_CONFLICT`. Identical normalized tool arguments in the
same conversation also resolve to the saved result when the provider generates a new call id. Results and request
ids cannot be replayed across actors, sessions or tools.

Optional `request_id` reuses an exact saved operation. Optional `request_key` distinguishes a deliberately
separate operation; reuse that key on retries. A re-check after newly supplied evidence needs the existing
`case_reference` and a new stable key. This prevents an accidental retry from repeating evaluation or callbacks
while still allowing intentional re-evaluation. Changed arguments under an existing request key are rejected.
The prompt and tool schemas describe this contract. This deduplicates normalized requests, not arbitrary
paraphrases or unrelated conversations; the model must retain the request identity when retrying an operation.

All database effects of a gateway tool now join one transaction: case/verification creation, evaluation,
review routing, callback creation, call logs, transcript linkage and saved result. The former inner commits
flush inside that transaction. A crash before commit rolls everything back; a crash or timeout after commit
is recovered by replay. No compensating workflow is needed because these tools have no external side effects.
Unexpected exceptions and concurrency conflicts roll back the execution claim, allowing a fresh retry.
Other domain failures roll back business work to a savepoint and store the failure response for replay.
Existing direct desk/API operations keep their original transaction boundaries.

## Rollout and verification

Run `uv run alembic upgrade head` before starting the updated backend. Migration `0002` adds three tables without
changing existing records. Publish the updated agent prompt through the normal provisioning command; realtime
function schemas are supplied by the backend on new sessions.

Automated tests use real migrations and inject duplicate concurrent calls, lost responses, process-level
service reconstruction, failures after evaluation/callback work, slow tools, interrupted turns, timeouts,
reconnects, invalid admission and replay. No paid telephone call or live provider contract test was performed
for this change. Before deployment acceptance, place a real inbound call and test interruption during a slow
lookup, provider reconnect, case-reference readback and the final transcript linkage.

Local validation: 380 Python tests passed against SQLite, including migration schema comparison and a populated
`0001` → `0002` upgrade; 36 JavaScript audio tests passed. Generated API documentation is current. PostgreSQL was
not run locally because Docker/psql were unavailable; the existing CI matrix includes PostgreSQL.

Keep the execution/receipt records for as long as retries are accepted. Removing them permits those operations
to execute again. They contain saved business results and require the same access and retention controls as
the case data. The media admission records also require an explicit retention policy at operational rollout.
