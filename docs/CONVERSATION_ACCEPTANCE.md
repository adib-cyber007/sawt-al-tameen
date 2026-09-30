# Conversation and document acceptance — 30 September 2026

The recovery version was pushed to `adib-cyber007/sawt-al-tameen` main at
`38f7a9d94c1b0290c32f32c868f2746e28827afd` before this work began.

## Actual hosted conversation results

These are real AssemblyAI model replies to a Windows synthetic spoken caller, via the public browser
WebSocket and real backend. They are not hand-written agent replies or transcription-only checks.

| Scenario | Duration | Caller turns | Actual business sequence | Result |
|---|---:|---:|---|---|
| Missing documents and same-case recheck | 236 s | 4 | Verify; request documents; log; actual upload of three files; recheck; log updated recommendation | Workflow passed |
| Wrong date of birth | 131 s | 3 | Readback; failed verification; callback and log; no coverage lookup | Workflow passed |

Both completed authoritative session timelines were persisted. The first case remained pending human review;
the agent explicitly described a recommendation needing a qualified reviewer. The failed-verification call
promised a callback and did not disclose benefits. No tool errors were recorded in these two final runs.
Provider timeline mean first-audio measurements were 1,956 ms and 2,010 ms respectively; these are small-sample
provider metrics, not browser playback latency or a guarantee. Confidence was reported as 1.0 even though
“Aisha” appeared as “Ayesha”, so confidence alone does not establish correctness.

Session references: `sess_006af90dae6c49a6a61fa6f4e461d70b` and
`sess_5fd360639b7b40b283d9c76052a136b3`. Full synthetic transcripts and checks are in
`.hosted/conversation-evaluation-v3.json` and are intentionally excluded from Git.

## Conversation quality findings

- The first attempt dropped a zero from the spoken policy identifier. The agent correctly rejected malformed
  input, but did not first confirm the identifier. Instructions now require identity readback, preservation of
  zeros, and correction of argument-format errors without restarting identification. Both final conversations
  read back identity details and waited for confirmation before verification.
- The failed-verification path previously attempted an unsupported callback reason. The prompt now specifies
  the existing `OTHER` value; the final call logged the callback successfully.
- Full procedure/cost/date readback was **not consistently followed**. In the successful document conversation,
  the agent performed its first coverage check immediately after collecting the request. Workflow success does
  not mean this conversational acceptance requirement passed. Further model/confirmation control needs care;
  no heuristic speech-confirmation gate was added that might reject valid natural caller turns.
- The final recommendation cited the benefit schedule and clearly reserved the final decision for a human.

## Browser interruption and echo

Device speakers is the default. Capture is protected throughout queued reply audio, network gaps, drain, and
room/device echo tail. Interrupt clears queued playback; stopped or stale reply packets do not leak into the
new reply. With headphones selected before the call, capture remains open for semantic interruption. Browser
AEC is requested in both modes. These tradeoffs are visible on the call page rather than silently changing
microphone behavior.

Audio tests exercise echo-cancellation failure at five device sample rates, late reply packets, ID-less manual
interruption, and headphones mode. Physical Brave speaker/microphone echo and human-accent conversations remain
manual acceptance tasks. The two live caller scenarios bypassed physical playback/capture and had no acoustic
interruptions; they do not establish real-device echo immunity.

## Document workflow and regression checks

The actual browser looked up a synthetic case, uploaded clinical notes, operative plan and prior treatment
record, refreshed the missing-document list from three to zero, and downloaded matching bytes. Desktop and
390-pixel mobile layouts had no overflow or JavaScript page errors. Invalid credentials were rejected.

Integration checks cover authentication, type/size rejection, exact-byte download, registration failure cleanup,
locked-case rejection, same-case recheck, completed transcript linkage, and human sign-off. This console uses the
existing operator gateway credential; production provider identity still belongs to the authenticating gateway.
File signatures and metadata are validated, not the clinical correctness of file contents.

Regression suite: 393 Python tests and 46 browser/audio tests passed. Uploaded files are private and Git-ignored;
the database and document directory must both be preserved when migrating hosting.
