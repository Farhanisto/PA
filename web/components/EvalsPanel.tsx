"use client";

import { useEffect, useState } from "react";
import { ApiError, evalApi, type EvalAllResult, type EvalSuiteResult } from "@/lib/api";
import { Badge, Card, EmptyState, ErrorBox, Skeleton, Spinner, Table } from "./ui";

function err(e: unknown): string {
  return e instanceof ApiError ? `Request failed (${e.status}): ${e.message}` : "Something went wrong.";
}

export function EvalsPanel() {
  const [suites, setSuites] = useState<string[] | null>(null);
  const [selected, setSelected] = useState("classification");
  const [useModel, setUseModel] = useState(false);
  const [result, setResult] = useState<EvalSuiteResult | null>(null);
  const [all, setAll] = useState<EvalAllResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    evalApi
      .suites()
      .then(setSuites)
      .catch((e) => setError(err(e)));
  }, []);

  async function runOne() {
    setBusy(true);
    setError("");
    setAll(null);
    try {
      setResult(await evalApi.run(selected, useModel));
    } catch (e) {
      setError(err(e));
    } finally {
      setBusy(false);
    }
  }

  async function runEvery() {
    setBusy(true);
    setError("");
    setResult(null);
    try {
      setAll(await evalApi.runAll(useModel));
    } catch (e) {
      setError(err(e));
    } finally {
      setBusy(false);
    }
  }

  const options = suites ?? ["classification", "summary", "draft", "authorization"];

  return (
    <Card
      title="AI evaluations"
      hint="Regression checks for triage, summaries, drafts, and authorization/isolation rules."
      actions={
        <>
          <button type="button" className="btn btn-sm" onClick={() => void runOne()} disabled={busy}>
            Run suite
          </button>
          <button type="button" className="btn btn-primary btn-sm" onClick={() => void runEvery()} disabled={busy}>
            Run all
          </button>
        </>
      }
    >
      <div className="row row-top">
        <div className="field" style={{ maxWidth: 240 }}>
          <label htmlFor="eval-suite">Suite</label>
          <select id="eval-suite" value={selected} onChange={(e) => setSelected(e.target.value)}>
            {options.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </div>
        <label className="small muted" style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <input type="checkbox" checked={useModel} onChange={(e) => setUseModel(e.target.checked)} />
          use live model
        </label>
      </div>

      {error ? <ErrorBox message={error} /> : null}
      {busy && !result && !all ? <Spinner label="Evaluating…" /> : null}

      {all ? (
        <div style={{ marginTop: 20 }}>
          <p className="row row-top">
            <Badge value={all.failed === 0 ? "pass" : "fail"} dot />
            <span>
              {all.passed}/{all.total} cases · score {(all.score * 100).toFixed(0)}%
            </span>
          </p>
        </div>
      ) : null}

      {result ? (
        <div style={{ marginTop: 20 }}>
          <p className="row row-top">
            <Badge value={result.failed === 0 ? "pass" : "fail"} dot />
            <span>
              {result.passed}/{result.total} · score {(result.score * 100).toFixed(0)}% ·{" "}
              {result.model_backed ? "model-backed" : "deterministic"}
            </span>
          </p>
          <Table head={["Case", "Result", "Detail"]}>
            {result.results.map((r) => (
              <tr key={r.name}>
                <td>
                  <span className="mono tiny">{r.name}</span>
                </td>
                <td>
                  <Badge value={r.passed ? "pass" : "fail"} />
                </td>
                <td className="small muted">{r.detail || "—"}</td>
              </tr>
            ))}
          </Table>
        </div>
      ) : null}

      {!all && !result && !busy && !suites ? <Skeleton rows={2} /> : null}
      {!all && !result && !busy && suites ? (
        <EmptyState title="No results yet" hint="Pick a suite and run it, or run all." />
      ) : null}
    </Card>
  );
}