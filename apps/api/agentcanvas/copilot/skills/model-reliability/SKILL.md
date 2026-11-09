---
name: model-reliability
description: Choose models and make model calls dependable - backups, timeouts, caching, cost - for production. Use when the person asks about cost, speed, outages, rate limits, choosing a model, or making a workflow production-ready.
---

# Dependable model calls

- One `model.chat` per model choice, shared by the steps that need it. `list_models` shows what is available;
  prefer the project's model unless the person wants another.
- `fallbacks`: models tried in order when this one fails (down, out of quota, too slow). Pick another provider, so
  one outage does not stop both (W016 warns when a backup repeats the model).
- `timeout` for slow providers; `max_tokens` to bound long answers and cost.
- `cache_ttl` answers identical requests from a cache: good for classification and extraction, not for chat.
- Cheaper, faster models suit classifying and routing; stronger ones suit writing and multi-step agents.
