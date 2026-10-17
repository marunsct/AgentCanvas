# Vendored Matura SDK

A verbatim copy of the Matura Python SDK (`sdk/python/src/matura` in marunsct/Platform, commit `44e2bda` plus its typing fix in the TS9a follow-up, version
0.1.0, Apache-2.0), without its FastAPI helpers: AgentCanvas keeps its own dependencies (`agentcanvas/api/deps.py`).

It is vendored because the Platform repository is private, so the package cannot be installed from it yet. Once
`matura` is published, replace this folder with the dependency `matura>=0.1` and change the imports from
`agentcanvas.vendor.matura` to `matura`.

Do not edit these files here: fix them in the Platform repository and copy them again.
