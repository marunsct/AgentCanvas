/** JSON Schema validation for the RJSF forms without `eval` (S12): `@cfworker/json-schema` interprets schemas,
 * where Ajv compiles them to functions, so the app runs under `script-src 'self'`. */
import { Validator as Interpreter, type OutputUnit } from "@cfworker/json-schema";
import {
  createErrorHandler, getDefaultFormState, toErrorList, toErrorSchema, unwrapErrorHandler, validationDataMerge,
  type CustomValidator, type ErrorTransformer, type RJSFSchema, type RJSFValidationError, type UiSchema,
  type ValidationData, type ValidatorType,
} from "@rjsf/utils";

// errors that only say "something below failed": the leaf error is reported on its own
const WRAPPERS = new Set(["properties", "items", "prefixItems", "allOf", "$ref", "false", "dependentSchemas", "contains"]);

const cache = new WeakMap<object, Interpreter>();

function interpreter(schema: RJSFSchema): Interpreter {
  let v = cache.get(schema);
  if (!v) {
    v = new Interpreter(schema as never, "2020-12", false);
    cache.set(schema, v);
  }
  return v;
}

const pathOf = (location: string) => location.replace(/^#/, "").split("/").filter(Boolean).map(decodeURIComponent);
const quoted = (text: string) => /"([^"]+)"/.exec(text)?.[1];

/** cfworker output units -> RJSF errors (property ".a.b", message, stack). */
export function toRjsfErrors(units: OutputUnit[]): RJSFValidationError[] {
  const located = new Set(units.filter((u) => !WRAPPERS.has(u.keyword)).map((u) => u.instanceLocation));
  const out: RJSFValidationError[] = [];
  for (const u of units) {
    if (WRAPPERS.has(u.keyword)) continue;
    let path = pathOf(u.instanceLocation);
    let message = u.error;
    if (u.keyword === "required") {
      const missing = quoted(u.error) ?? "";
      path = [...path, missing];
      message = `must have required property '${missing}'`;
    } else if (u.keyword === "additionalProperties") {
      const extra = quoted(u.error) ?? "";
      const at = `${u.instanceLocation}/${extra}`;  // a declared property that failed its own schema is not "extra"
      if ([...located].some((l) => l === at || l.startsWith(`${at}/`))) continue;
      path = [...path, extra];
      message = "must NOT have additional properties";
    }
    const property = path.length ? `.${path.join(".")}` : "";
    out.push({ name: u.keyword, property, message, stack: `${property} ${message}`.trim(), schemaPath: u.keywordLocation });
  }
  return out;
}

export class InterpretingValidator implements ValidatorType {
  rawValidation<Result = unknown>(schema: RJSFSchema, formData?: unknown): { errors?: Result[]; validationError?: Error } {
    try {
      return { errors: toRjsfErrors(interpreter(schema).validate(formData).errors) as Result[] };
    } catch (e) {
      return { validationError: e as Error };
    }
  }

  validateFormData(
    formData: unknown, schema: RJSFSchema, customValidate?: CustomValidator, transformErrors?: ErrorTransformer,
    uiSchema?: UiSchema,
  ): ValidationData<unknown> {
    const raw = this.rawValidation<RJSFValidationError>(schema, formData);
    let errors = raw.errors ?? [];
    if (raw.validationError) errors = [...errors, { stack: raw.validationError.message }];
    if (transformErrors) errors = transformErrors(errors, uiSchema);
    const errorSchema = toErrorSchema(errors);
    if (!customValidate) return { errors, errorSchema };
    const full = getDefaultFormState(this, schema, formData, schema, true);
    const handler = customValidate(full, createErrorHandler(full), uiSchema, errorSchema);
    return validationDataMerge({ errors, errorSchema }, unwrapErrorHandler(handler));
  }

  isValid(schema: RJSFSchema, formData: unknown, rootSchema: RJSFSchema): boolean {
    try { // sub-schemas refer to the root's definitions: validate with them in scope
      const scoped = { ...schema, definitions: rootSchema.definitions, $defs: (rootSchema as { $defs?: unknown }).$defs };
      return interpreter(scoped as RJSFSchema).validate(formData).valid;
    } catch {
      return false;
    }
  }

  toErrorList = toErrorList;
}

export const validator = new InterpretingValidator();
