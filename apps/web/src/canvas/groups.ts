/** Visual groups (layout frames): labelled boxes around steps, stored in `metadata.groups`. Pure; unit-tested.
 *
 * A frame is only a picture — it changes nothing about how the workflow runs (unlike a subgraph). It sizes
 * itself around its members, so moving a step moves the frame's edge with it.
 */
import type { WF } from "../ir/model";

export interface Group {
  id: string;
  label: string;
  color: string;
  members: string[];
}

export interface Rect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export const GROUP_COLORS = ["#2563eb", "#16a34a", "#d97706", "#9333ea", "#dc2626", "#0891b2"];
const PAD = 24;
const LABEL = 22;

/** `metadata.groups` is free-form (imported or hand-edited), so every field is checked before use. */
function isGroup(g: unknown): g is Group {
  if (typeof g !== "object" || g === null) return false;
  const { id, label, color, members } = g as Record<string, unknown>;
  return typeof id === "string" && typeof label === "string" && typeof color === "string"
    && Array.isArray(members) && members.every((m) => typeof m === "string");
}

export function groupsOf(wf: WF): Group[] {
  const raw = (wf.metadata as { groups?: unknown } | undefined)?.groups;
  if (!Array.isArray(raw)) return [];
  const known = new Set([...wf.nodes.map((n) => n.id), ...wf.resources.map((r) => r.id)]);
  return raw
    .filter(isGroup)
    .map((g) => ({ ...g, members: g.members.filter((m) => known.has(m)) }))
    .filter((g) => g.members.length > 0); // a frame whose steps were all deleted disappears
}

/** The workflow metadata with one more group (members already framed elsewhere move to it). */
export function addGroup(wf: WF, id: string, label: string, members: string[]): Record<string, unknown> {
  const others = groupsOf(wf).map((g) => ({ ...g, members: g.members.filter((m) => !members.includes(m)) }));
  const color = GROUP_COLORS[others.length % GROUP_COLORS.length];
  return { ...(wf.metadata ?? {}), groups: [...others.filter((g) => g.members.length), { id, label, color, members }] };
}

export function removeGroup(wf: WF, id: string): Record<string, unknown> {
  return { ...(wf.metadata ?? {}), groups: groupsOf(wf).filter((g) => g.id !== id) };
}

export function renameGroup(wf: WF, id: string, label: string): Record<string, unknown> {
  return { ...(wf.metadata ?? {}), groups: groupsOf(wf).map((g) => (g.id === id ? { ...g, label } : g)) };
}

/** The frame around member rectangles (with padding and room for the label), or null without members. */
export function frameAround(rects: Rect[]): Rect | null {
  if (!rects.length) return null;
  const x = Math.min(...rects.map((r) => r.x)) - PAD;
  const y = Math.min(...rects.map((r) => r.y)) - PAD - LABEL;
  const right = Math.max(...rects.map((r) => r.x + r.width)) + PAD;
  const bottom = Math.max(...rects.map((r) => r.y + r.height)) + PAD;
  return { x, y, width: right - x, height: bottom - y };
}
