# Framework Adapter Specifications

Each file specifies one adapter against the SPI in [doc 12 §C.2](../12-multi-framework-technical-spec.md#c2-adapter-spi-normative). All APIs cited were verified against the framework's source repository on **2026-09-28** (versions in doc 12 §A.2).

| Adapter | Version baseline | Target tier | Phase | Spec |
|---------|------------------|-------------|-------|------|
| LangChain / LangGraph / Deep Agents | 1.4.2 / 1.2.12 / 0.7.19 | T1 | R1 | docs 03, 04, 07 (reference adapter) |
| OpenAI Agents SDK | 0.22.3 | T1 agent tier · T2 graph tier | F2a | [openai-agents-sdk.md](./openai-agents-sdk.md) |
| Google ADK | 2.10.0 | T1 | F2b | [google-adk.md](./google-adk.md) |
| Microsoft Agent Framework | python-1.19.0 | T1 | F3 | [microsoft-agent-framework.md](./microsoft-agent-framework.md) |
| CrewAI | 1.15.22 | T1 (Flows) · T2 (Crews) | F4 | [crewai.md](./crewai.md) |
| Pydantic AI | 2.51.0 | T2 → T1 | F5 | [pydantic-ai.md](./pydantic-ai.md) |
| Strands Agents | 1.41.0 | T2 | F5 | [strands.md](./strands.md) |
| LlamaIndex Workflows | 2.11.1 | T2 | F5 | [llamaindex-workflows.md](./llamaindex-workflows.md) |
| Mastra (TypeScript sidecar) | @mastra/core 1.72 | T2 | F6 | [mastra.md](./mastra.md) |
| Claude Agent SDK & other wrapped agents | 0.2.160 | T3 | F1 | [wrapped-agents.md](./wrapped-agents.md) |
| Agent Spec bridge & interchange | 26.3.1 | bridge / interchange | F1 | [agent-spec-bridge.md](./agent-spec-bridge.md) |

Every adapter spec follows the same structure:
1. Verified baseline · 2. Positioning · 3. Core IR mapping · 4. Dialect nodes · 5. Code generation · 6. HITL · 7. State & persistence · 8. Events & tracing · 9. Hosting · 10. Import / round-trip · 11. Gaps & emulations · 12. Work plan & effort · 13. Risks
