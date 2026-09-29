# @agentcanvas/ir-schema

Generated — do not edit. JSON Schema of the Graph IR (`ir.schema.json`) and the built-in node manifests
(`node-types.json`), exported from the Pydantic models in `apps/api`:

```bash
cd apps/api && uv run python scripts/export_ir_schema.py
```

The web app generates its TypeScript types from `ir.schema.json` (`pnpm gen:ir`). CI fails if the committed files
are stale.
