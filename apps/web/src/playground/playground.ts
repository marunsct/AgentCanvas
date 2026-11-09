/** Playground helpers (S31): which settings the chosen model takes. */
import type { ModelInfo } from "../api/generated";

/** False when the model catalog says the model refuses sampling settings (current Claude models, reasoning
 * models): a temperature would be left out anyway (W020). A model the catalog does not list may take one. */
export function takesTemperature(models: ModelInfo[] | undefined, id: string): boolean {
  return models?.find((m) => m.id === id)?.sampling !== false;
}
