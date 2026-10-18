"use client";

import { useCallback, useEffect, useState } from "react";
import { ApiError, jobApi, obsApi, type Job, type ObservabilitySummary } from "@/lib/api";
import { Badge, Card, EmptyState, ErrorBox, Notice, Skeleton, Table, usd } from "./ui";

function err(e: unknown): string {
  return e instanceof ApiError ? `Request failed (${e.status}): ${e.message}` : "Something went wrong.";
}

export function ObservabilityPanel() {
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [selected, setSelected] = useState("");
  const [trace, setTrace] = useState<ObservabilitySummary | null>(null);
  const [costs, setCosts] = useState<Awaited<ReturnType<typeof obsApi.costs>> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const loadOverview = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [j, c, k] = await Promise.all([jobApi.list(20), obsApi.toolCalls(25), obsApi.costs()]);
      setJobs(j);
      setCosts(k);
      setSelected((prev) => (prev && j.some((x) => x.job_id === prev) ? prev : (j[0]?.job_id ?? "")));
    } catch (e) {
      setError(err(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadOverview();
  }, [loadOverview]);

  useEffect(() => {
    if (!selected) {
      setTrace(null);
      return;
    }
    let live = true;
    obsApi
      .job(selected)
      .then((t) => live && setTrace(t))
      .catch((e) => live && setError(err(e)));
    return () => {
      live = false;
    };
  }, [selected]);

  return (
    <Card
      title="Agent traces"
      hint="Every run: node latency, tool calls, failures, token usage, and estimated cost."
      actions={
        <button type="button" className="btn btn-sm" onClick={() => void loadOverview()} disabled={loading}>
          Refresh
        </button>
      }
    >
      {costs ? (
        <Notice tone="info">
          {costs.runs} runs · {costs.total_tokens} tokens · {usd(costs.total_cost_usd)} estimated
        </Notice>
      ) : null}
      {error ? <ErrorBox message={error} onRetry={() => void loadOverview()} /> : null}

      {loading && !jobs ? <Skeleton rows={3} /> : null}

      {jobs && jobs.length > 0 ? (
        <>
          <div className="field" style={{ marginTop: 20, maxWidth: 420 }}>
            <label htmlFor="obs-job">Job</label>
            <select id="obs-job" value={selected} onChange={(e) => setSelected(e.target.value)}>
              {jobs.map((j) => (
                <option key={j.job_id} value={j.job_id}>
                  {j.job_id.slice(0, 8)} · {j.status} · {j.request.slice(0, 48)}
                </option>
              ))}
            </select>
          </div>

          {trace ? (
            <>
              <p className="small muted" style={{ marginTop: 16 }}>
                {trace.total_latency_ms} ms total · {usd(trace.total_cost_usd)} ·{" "}
                {trace.failures} failure{trace.failures === 1 ? "" : "s"}
              </p>
              <Table head={["Step", "Status", "Latency", "Tokens", "Cost"]}>
                {trace.traces.map((t) => (
                  <tr key={t.id}>
                    <td>{t.node}</td>
                    <td>
                      <Badge value={t.status} dot />
                    </td>
                    <td>{t.latency_ms} ms</td>
                    <td className="mono tiny">
                      {t.prompt_tokens}→{t.completion_tokens}
                    </td>
                    <td>{usd(t.cost_usd)}</td>
                  </tr>
                ))}
                {trace.tool_calls.map((c) => (
                  <tr key={c.id}>
                    <td>
                      <span className="mono tiny">{c.tool_name}</span>
                    </td>
                    <td>
                      <Badge value={c.status} dot />
                    </td>
                    <td>{c.latency_ms} ms</td>
                    <td className="muted">—</td>
                    <td className="muted">{c.error ? "error" : "—"}</td>
                  </tr>
                ))}
              </Table>
            </>
          ) : (
            <EmptyState title="No trace selected" hint="Pick a job above to see its run." />
          )}
        </>
      ) : jobs && jobs.length === 0 ? (
        <EmptyState title="No jobs yet" hint="Submit work from Inbox and it will appear here." />
      ) : null}
    </Card>
  );
}