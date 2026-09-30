# Presentation narration

Synthetic narration accompanies actual application screenshots and documented acceptance results. It is not a recording of a live agent call.

## Slide 1

Sawt al Tameen is the voice of insurance. It is an English language pre authorisation agent built on AssemblyAI. A clinic describes a request by voice, and the assistant prepares an auditable case for a qualified human reviewer. This presentation uses synthetic narration and fictional demonstration data.

## Slide 2

A provider desk must collect the right member details, identify a treatment and understand which supporting documents are required. Missing information creates follow up work. Our prototype guides that intake conversation and makes the next step explicit. The intended users are insurer intake staff and provider authorisation desks.

## Slide 3

The public browser demo requires no account. Choose device speakers for echo protection, or headphones for spoken interruption. Start a call and speak the fictional provider and member identifiers. Live caller captions and assistant replies appear in one conversation. You can interrupt or type a correction when a detail is wrong.

## Slide 4

AssemblyAI handles the hosted voice interaction through its Voice Agent API, including speech recognition, the managed conversational model and spoken output. Our server bridges twenty four kilohertz browser audio and executes three schema defined tools. Verification gates coverage checks. A deterministic rules engine reads the same benefit catalogue used for citations. Credentials stay on the server.

## Slide 5

This screenshot comes from the authenticated document acceptance run. The browser uploaded clinical notes, an operative plan and a prior treatment record, then downloaded matching bytes. In the actual AssemblyAI conversation, the assistant requested the missing documents and rechecked the same case after upload. The resulting recommendation still required human review. The document desk needs an operator access key.

## Slide 6

The agent cannot approve or deny a request because none of its three tools provides that action. A reviewer only API and the case state machine enforce the final decision boundary. For a voice linked case, sign off remains blocked until the completed transcript is stored. The system keeps the original recommendation and the human decision separately in the audit trail.

## Slide 7

On September thirtieth, all three hundred and ninety three Python tests and forty six JavaScript browser and audio tests passed. Twenty six public deployment checks passed. Two actual AssemblyAI conversations with a synthetic spoken caller exercised document upload and recheck, and failed verification with a callback. These are small sample workflow results. Physical speaker echo and varied human accents still need evaluation, and full procedure readback remains inconsistent.

## Slide 8

Our business hypothesis is fewer incomplete handoffs and less repeated intake work. We would offer a paid pilot to an insurer desk, measuring handling time, document completeness, identifier accuracy and cost per completed case. There are no measured savings or clinical outcomes yet. The prototype uses only fictional policies and records. A live Twilio number is not configured, and the current demonstration host must stay online during judging.

## Slide 9

You can test the voice agent online using the judge guide and fictional caller details. The public MIT licensed repository includes setup instructions, tests, the rules catalogue and conversation evidence. Sawt al Tameen brings a spoken request into an accountable review process. Prepared by the assistant, decided by a person.
