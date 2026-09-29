// Generate TypeScript types for API request/response bodies from the OpenAPI component schemas.
// (openapi-typescript needs the TypeScript compiler JS API, which TypeScript 7 no longer ships.)
import { readFileSync, writeFileSync } from "node:fs";
import { compile } from "json-schema-to-typescript";

const openapi = JSON.parse(readFileSync("../../packages/ir-schema/openapi.json", "utf8"));
const defs = JSON.parse(JSON.stringify(openapi.components.schemas).replaceAll("#/components/schemas/", "#/$defs/"));
const root = {
  title: "ApiSchemas",
  type: "object",
  additionalProperties: false,
  properties: Object.fromEntries(Object.keys(defs).map((k) => [k, { $ref: `#/$defs/${k}` }])),
  $defs: defs,
};
const ts = await compile(root, "ApiSchemas", {
  bannerComment: "/* Generated from packages/ir-schema/openapi.json (pnpm gen:api). Do not edit. */",
  additionalProperties: false,
  unreachableDefinitions: true,
});
writeFileSync("src/api/generated.ts", ts);
console.log(`src/api/generated.ts: ${Object.keys(defs).length} schemas`);
