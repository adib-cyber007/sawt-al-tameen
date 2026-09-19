# Synthetic UAE pre-authorisation knowledge base

> SYNTHETIC TEST DATA. Fictional insurer, plans, providers, members and procedure codes. Structurally modelled on UAE health-insurance regulation (DHA/DOH) for demonstration only. Not real policy data and not valid for any real authorisation decision.

Demo and test data for a pre-authorisation voice agent: policy tiers, procedure coverage, network providers,
provider onboarding requirements, sample members, and the rules that decide when a case must go to a human.

## Files

| File | Contents | Keys that link it to the others |
|---|---|---|
| `policy_tiers.json` | 4 tiers with annual and sub-limits, networks, co-payments, pre-authorisation thresholds and waiting periods | `tier_id`, `network.network_id` |
| `procedure_coverage.json` | 50 procedures with per-tier cover, pre-authorisation flags, exclusions, waiting periods and escalation reasons | `coverage_by_tier.<tier_id>`, `specialty_required`, `escalation.escalation_rule_id` |
| `network_providers.json` | 20 fictional facilities across Dubai, Abu Dhabi and Sharjah | `networks[]`, `specialties[]`, `status` |
| `supplier_onboarding_requirements.json` | Onboarding checklist and three in-flight applications | `requirement_id`, `provider_id` |
| `sample_members.json` | 20 members with dependants, tiers and policy status | `tier`, `policy_number`, `member_id` |
| `escalation_rules.md` | Plain-language description of what is *not* rule-based | `ESC-001` … `ESC-008` |
| `escalation_rules.json` | The same rules, machine-readable; the rules engine cites this text verbatim | `rule_id` |
| `schedule-<tier>.md` | Per-tier schedule of benefits; the document coverage decisions cite | `Section 4.n <code>` |

## How they fit together

```
sample_members.json ──tier──> policy_tiers.json <──network──> network_providers.json
                                     │                                  │
                          coverage_by_tier / thresholds        specialty_required
                                     ▼                                  │
                         procedure_coverage.json <───────────────────────┘
                                     │
                     escalation.escalation_rule_id
                                     ▼
                           escalation_rules.md
```

A coverage lookup runs: verify the member and read their tier → find the procedure → read `coverage_by_tier` for
that tier → check the amount against the tier threshold and sub-limit → check the provider is active in that tier's
network → if the procedure is `AMBIGUOUS`, or any escalation rule fires, hand to a human.

## Tiers at a glance

| Tier | Annual limit (AED) | Network | Pre-auth threshold (AED) | Outpatient co-pay |
|---|---|---|---|---|
| Basic | 150,000 | Basic Network | 1,000 | 20% |
| Enhanced | 500,000 | Enhanced Network | 2,500 | 20% |
| Comprehensive | 1,000,000 | Comprehensive Network | 5,000 | 10% |
| Executive | 3,000,000 | Executive Network | 10,000 | 0% |

Networks are nested: Basic ⊂ Enhanced ⊂ Comprehensive ⊂ Executive. Only Executive covers out-of-network care.

## Deliberate test cases

Of 50 procedures: **36 clear**, **3 excluded on every tier**, and
**11 ambiguous**. The ambiguous ones exist to trigger escalation rather than a clean
approve/deny, and each names the escalation rule it should raise.

Useful demo calls:
- Clear approval path: MRI knee `SP-10050` for an active Enhanced member at an active provider.
- Missing documentation: biologic therapy `SP-60050` (ESC-001).
- Excluded: cosmetic rhinoplasty `SP-20140`.
- Tier boundary: total knee replacement `SP-20050` for a Basic member (benefit starts at Enhanced).
- Eligibility: any request for a lapsed member (`MBR-2026-0008`, `MBR-2026-0013`, `MBR-2026-0020`) — ESC-007.
- Provider problem: a request from `PRV-30020` (suspended) or `PRV-30016` (onboarding) — ESC-008.

## Using the catalogue with AssemblyAI

The catalogue stays in this application; it is not uploaded to the voice provider. Load it into the application
database, then provision the AssemblyAI agents:

```bash
uv run python -m preauth.seed
uv run python scripts/assemblyai_setup.py
```

`assemblyai_setup.py` derives recognition keyterms from the catalogue, but coverage facts, rules and citations
reach the agent only through the application's three constrained tools. This keeps the database/rules engine as
the authoritative source and prevents provider-side document retrieval from bypassing application guardrails.

## Consistency and regeneration

These files are generated and validated by `scripts/generate_uae_knowledge_base.py`; edit that script, not the
JSON. Validation enforces: per-tier pre-authorisation flags match each procedure's rule and the tier threshold;
excluded procedures never require pre-authorisation; every referenced tier, network and escalation rule exists;
networks nest correctly; every clearly-decidable procedure's specialty is offered by an active provider; amounts
sit within tier limits; and member tiers, policy numbers and dependant records are valid and unique.

```bash
uv run python scripts/generate_uae_knowledge_base.py --check
```

## How the backend uses these files

`preauth.seed` loads this catalogue into the database, and the rules engine evaluates those tables. Every citation
the agent reads out comes from a tool result and resolves to a section that exists here: the per-tier schedules
carry the `Section 4.n` headings the coverage rules cite, and `escalation_rules.md` carries the ESC-### text an
escalation quotes.

There is no second source of coverage data anywhere in the repository. Editing this catalogue (through the
generator) changes what the agent decides.
