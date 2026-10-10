/** Template gallery (S23): categories in a stable order and the search/category filter, kept free of React. */
import type { TemplateInfo } from "../api/types";

/** The order categories appear in; a category the server adds later goes after these, "Other" last. */
export const CATEGORY_ORDER = [
  "Getting started", "Customer support", "Content", "Sales", "Data extraction", "Research", "HR", "Engineering",
  "Operations",
];
export const OTHER = "Other";

export type GalleryTemplate = Pick<TemplateInfo, "id" | "name" | "description"> &
  Partial<Pick<TemplateInfo, "category" | "tags" | "difficulty">>;

export const categoryOf = (t: GalleryTemplate): string => t.category || OTHER;

/** The categories present, in display order, with how many templates each has. */
export function categories(templates: GalleryTemplate[]): { name: string; count: number }[] {
  const counts = new Map<string, number>();
  for (const t of templates) counts.set(categoryOf(t), (counts.get(categoryOf(t)) ?? 0) + 1);
  const rank = (c: string) => (c === OTHER ? 1e9 : CATEGORY_ORDER.includes(c) ? CATEGORY_ORDER.indexOf(c) : 1e6);
  return [...counts].map(([name, count]) => ({ name, count }))
    .sort((a, b) => rank(a.name) - rank(b.name) || a.name.localeCompare(b.name));
}

/** Templates in `category` (null: all) whose name, description, category or tags contain every search word. */
export function filterTemplates<T extends GalleryTemplate>(templates: T[], query: string, category: string | null): T[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  return templates.filter((t) => {
    if (category && categoryOf(t) !== category) return false;
    const text = [t.name, t.description, categoryOf(t), t.difficulty ?? "", ...(t.tags ?? [])].join(" ").toLowerCase();
    return words.every((w) => text.includes(w));
  });
}
