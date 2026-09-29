# Sawt al-Tameen interface implementation plan

## Scope
Refresh the hosted /voice page and local /local console. Preserve all existing element IDs, script URLs, initial disabled/hidden controls, API contracts, audio code and human-review boundaries. Existing uncommitted work is retained. Changes are HTML and CSS only; no new packages, external fonts or network requests.

## Research — 28 September 2026
- [Cura / Calm Clinic](https://sleek.design/templates/healthcare-app): light healthcare surfaces, teal actions and readable conversations. Adopt the calm palette, not its appointment workflow.
- [Clinexa medical dashboard](https://themeforest.net/item/clinexa-medical-healthcare-management-dashboard-html-template/63810000): published healthcare administration theme. Separate the workspace from supporting information, without importing template code.
- [Vapi voice-agent workspace](https://docs.vapi.ai/debugging): conversation testing and transcript inspection reinforce visible call state and a prominent transcript.

## Design
An approachable insurance call desk: a teal voice-mark, white workspace, spacious transcript and preparation guidance. Palette: canvas #F3F7F8, surface #FFFFFF, ink #18353D, secondary ink #536B73, teal #006B63, soft teal #E7F3EF. Errors and warnings use semantic colors. Typography uses locally available Segoe UI / system sans, with a larger, lightly weighted page title.

Desktop layout, aligned left:
Brand                                       Deployment label
Page title                                  Language / currency
Conversation workspace                      Preparation / case details
Transcript                                  Supporting instructions
Call controls and status                    Human-review explanation

On mobile, stack conversation above support. Controls wrap, long text breaks and the transcript scrolls independently. Visible keyboard focus, semantic headings, live status regions, 44px button targets and no automatic motion.

## Plan review
A generic hospital dashboard adds irrelevant charts and navigation. Revised the concept around the actual pre-authorisation conversation and reviewer handoff. Hosted users get a checklist; local operators retain live case fields. Omit navigation between modes because deployments can enable either independently.

## Implementation
1. Preserve current JavaScript hashes and markup hooks.
2. Restructure both pages with matching responsive styles.
3. Run existing audio and Python regression suites and verify JavaScript hashes.
4. Inspect desktop/mobile layouts, initial controls, long transcripts and error presentation without a real insurance call.

No backend changes or service restart are part of this UI update.

## Verification results
- 346 Python tests passed; five SQLite datetime-adapter deprecation warnings.
- 36 existing audio tests passed.
- Hosted audio/application scripts and local application script are byte-for-byte unchanged from the working tree at task start.
- Browser preview at 320, 390, 768 and 1440px: no horizontal page overflow on either interface.
- Checked initial hosted control states, partial/final transcript rendering, long unbroken transcript text, error status styling, local case/document rendering and keyboard skip-link focus.
- Desktop and mobile screenshots inspected. Preview used intercepted static files and an intentionally unavailable local engine; no real microphone call or case creation was performed.
- Existing service was not restarted. Refresh the existing /voice or /local page to load these assets.

## Reference theme v2 — 29 September 2026
This revision supersedes the original teal visual direction. The user supplied sawt-al-tameen-redesign-v2.html and its screenshot as the design reference.

Applied navy #1A1848, lavender #E7E3F6, pearl #F6F4FB and amber #F2A93B; reused the reference's embedded Bricolage Grotesque and Figtree fonts in local fonts.css assets. Added the static microphone illustration, preparation strip, pill controls and navy human-review panel. Both hosted and local interfaces use this theme. The local console retains its existing case overview, composer, recording and transcript storage controls. Reference content is treated as visual input only.

The decoration stays static, checklist markers remain informational, and fonts are served locally. Added mobile adaptations, readable semantic error/success colours and focus outlines. The decorative amber ring is positioned below the review text for readability.

Verification after this revision:
- 346 Python tests passed (five existing SQLite datetime-adapter warnings).
- 36 audio tests passed.
- Every JavaScript file is byte-for-byte unchanged from the start of this revision.
- HTML contract comparison confirms all prior IDs, element types, initial disabled/hidden states and script URLs are unchanged.
- Both pages fit 320, 390, 768, 960 and 1440px widths without horizontal overflow.
- Browser interaction with simulated API responses verified local start, send and finish requests, transcript display and restored controls.
- Simulated microphone denial verified hosted error feedback and recovery controls.
- Long and partial/final transcript rendering checked on mobile.
- No actual microphone conversation, paid voice-service call or production case was created. Browser workflow checks used simulated responses; backend behavior is covered by the existing regression suite.
- Final screenshots: docs/ui/voice-theme-v2-desktop.png, docs/ui/voice-theme-v2-mobile.png, docs/ui/local-theme-v2-desktop.png and docs/ui/local-theme-v2-mobile.png. Local screenshot uses simulated engine capabilities.

Usage remains unchanged: on /voice, Start call, permit the microphone, wait for Listening, speak, then End call. On /local, Start call, type and Send or Record, then End call & store transcript. Only a qualified human reviewer makes a final decision.

## Project-feature alignment audit — 29 September 2026
- AED is the existing treatment-cost unit, established by CheckCoverageRuleInput.estimated_cost_aed, CoverageCheckCommand.estimated_cost_aed and the voice prompt. It is not a selectable currency, payment flow or new subfeature.
- Removed the standalone AED chip from both interfaces to avoid suggesting a currency option. Retained "Estimated cost in AED" in the hosted preparation guidance because it corresponds to the actual intake field.
- Clarified local voice recording is available when configured; retained the existing disabled-state and capability checks.
- The local console predates this redesign. Its route is still registered only when local_runtime exists. Hosted UI remains gated by assemblyai_enabled. No deployment mode was added or enabled by the UI work.
- Human-only approval/denial remains enforced by the existing state machine and reviewer service; the UI adds no decision controls or agent tools.
- These are software feature and project-rule checks, not a legal or regulatory compliance certification.
- After the alignment edits, 116 targeted architecture, state-machine, rules, reviewer, agent-tool, local API and API-documentation tests passed. HTML hooks and JavaScript hashes remain unchanged. Desktop/mobile screenshots were refreshed and checked for overflow and removal of the AED chip.
