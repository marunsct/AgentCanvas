import { ApiError } from "./client";

/** Whether a failed query is asked once more: a dropped connection or a server error may pass, but a 4xx (not found,
 * not allowed, invalid) will answer the same again, so the person is told at once instead of after a wait. */
export function shouldRetry(failureCount: number, error: unknown): boolean {
  if (failureCount >= 1) return false;
  return !(error instanceof ApiError && error.status >= 400 && error.status < 500);
}
