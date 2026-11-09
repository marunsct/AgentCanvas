---
name: helper
description: "Designed on AgentCanvas: the block \"helper\" of the workflow \"Orders\"."
model: claude-opus-5-5
tools:
  - type: agent_toolset_20260401
    default_config: {enabled: false, permission_policy: {type: always_allow}}
    configs:
      - {name: read, enabled: true}
  - type: custom
    name: lookup_order
    description: "Find an order."
    input_schema:
      type: object
      properties:
        order_id: {type: string, description: "the order number"}
        full: {type: boolean}
        tags: {type: array, items: {type: string}}
      required:
        - order_id
  - type: custom
    name: weather
    description: "Weather for a city"
    input_schema:
      type: object
      properties: {city: {type: string}}
      required:
        - city
  - type: mcp_toolset
    mcp_server_name: crm
    default_config: {permission_policy: {type: always_allow}}
    configs:
      - {name: delete_customer, permission_policy: {type: always_ask}}
mcp_servers:
  - {type: url, name: crm, url: "https://mcp.example.com/mcp"}
skills:
  - ./skills/tone
---

You help.
