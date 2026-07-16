"use client";

import useSWR from "swr";
import { errorHandlingFetcher } from "@/lib/fetcher";
import { SWR_KEYS } from "@/lib/swr-keys";
import { buildApiPath } from "@/lib/urlBuilder";

export interface UsageExportTotals {
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cost_cents: number;
}

export interface UsageExportUser {
  email: string;
  totals: UsageExportTotals;
}

export interface UsageExportResponse {
  start: string;
  end: string;
  users: UsageExportUser[];
}

export interface UsageExportRange {
  from?: Date;
  to?: Date;
}

// The export takes `date` params; local calendar date, not toISOString()
// (which shifts across the UTC boundary for non-UTC users).
function toDateParam(d: Date): string {
  const month = `${d.getMonth() + 1}`.padStart(2, "0");
  const day = `${d.getDate()}`.padStart(2, "0");
  return `${d.getFullYear()}-${month}-${day}`;
}

/**
 * Company-wide per-user usage (admin). GET /api/admin/usage/export.
 * `range` bounds the report (end-inclusive); omitted bounds fall back to the
 * backend default (trailing 30 days). Mutate the returned `refetch` after a
 * reset to revalidate the table.
 */
export function useUsageExport(range?: UsageExportRange) {
  const url = buildApiPath(SWR_KEYS.adminUsageExport, {
    start: range?.from ? toDateParam(range.from) : undefined,
    end: range?.to ? toDateParam(range.to) : undefined,
  });
  const { data, error, isLoading, mutate } = useSWR<UsageExportResponse>(
    url,
    errorHandlingFetcher,
    // keepPreviousData: a range change swaps in place of unmounting the table.
    { revalidateOnFocus: false, keepPreviousData: true }
  );

  return { usage: data, isLoading, error, refetch: mutate };
}

/**
 * Clears a user's current-window usage to lift a budget block (prior windows
 * are preserved). POST /api/admin/usage/reset.
 * @throws Error with the API detail message on failure.
 */
export async function resetUserUsage(userEmail: string): Promise<void> {
  const response = await fetch(SWR_KEYS.adminUsageReset, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_email: userEmail }),
  });

  if (!response.ok) {
    const data = await response.json().catch(() => null);
    throw new Error(data?.detail || data?.error_code || response.statusText);
  }
}
