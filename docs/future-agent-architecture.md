# Future agent architecture (roadmap — NOT implemented)

## Current

```
MoneyPro → Power BI → REST → Validation → Report Engine → Human Release
```

A single controlled pipeline: deterministic code calculates and validates; Claude interprets validated figures and writes the narrative; a human releases.

## Possible evolution

```
Financial Intelligence Orchestrator
        │
        ├── Data Agent            readiness, MoneyPro control, REST queries (through the existing pipeline)
        ├── Analysis Agent        interprets validated figures; drafts commentary
        ├── Research Agent        external context (markets, rates) clearly separated from personal data
        ├── Reporting Agent       assembles report_data; calls the engine
        └── QA/Governance Agent   gates, regression, QA, release checklist
```

## Rule

> **Agents interpret and orchestrate. The deterministic engine calculates. A human approves release.**

The separation is intentional:

- Financial values must be reproducible and auditable. They come only from DAX and validation code, never from model generation.
- Agents can fail, hallucinate or be prompt-injected. The gates (readiness, validation, regression, QA, Human Release) stay outside the agents and cannot be bypassed by them.
- Publication is irreversible from the user's point of view, so it remains a human decision.

## Preconditions before starting

1. `MONTHLY PIPELINE READY` sustained for several real months.
2. CI on non-private tests and the repository safety check.
3. Clear interfaces: each agent calls existing pipeline commands, with no duplicated logic.

## Relationship with Finance Intelligence Platform

A separate project (Finance Intelligence Platform: financial knowledge repository, semantic financial layer, REST APIs, financial analyst agent, executive context builder, prompt manager, local LLM, executive reports). The repositories stay **separate**. In the future they could share:

- a semantic financial layer (definitions such as Cash Basis, runway, net worth);
- financial knowledge (methodology documents);
- the deterministic financial engine (as a library or service);
- agent orchestration concepts.

No code is imported between the projects without explicit authorization.
