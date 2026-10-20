"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ApiError,
  appApi,
  type Connection,
  type ToolkitTool,
  type ToolkitTools,
} from "@/lib/api";
import { Badge, Card, EmptyState, ErrorBox, Skeleton } from "./ui";

function err(e: unknown): string {
  return e instanceof ApiError ? `Request failed (${e.status}): ${e.message}` : "Something went wrong.";
}

/** Group tools by the action verb in their slug, e.g. HUBSPOT_LIST_CONTACTS -> List. */
function actionOf(slug: string): string {
  const parts = slug.split("_");
  const verb = parts.find((p) =>
    /^(LIST|GET|CREATE|UPDATE|DELETE|SEARCH|ARCHIVE|LOG|BATCH|SEND|ADD|REMOVE|QUERY|EXPORT|IMPORT|MOVE|MERGE|CONVERT)$/i.test(p)
  );
  return (verb ?? "Other").replace(/^\w/, (c) => c.toUpperCase());
}

const ACTION_ORDER = [
  "List", "Get", "Search", "Query", "Create", "Update", "Log", "Add",
  "Send", "Move", "Convert", "Merge", "Remove", "Archive", "Delete",
  "Batch", "Export", "Import", "Other",
];

export function AppWorkspace({
  onAskAssistant,
  onGoToApps,
}: {
  onAskAssistant: (prompt: string) => void;
  onGoToApps: () => void;
}) {
  const [connections, setConnections] = useState<Connection[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [bundle, setBundle] = useState<ToolkitTools | null>(null);
  const [loadingList, setLoadingList] = useState(true);
  const [loadingTools, setLoadingTools] = useState(false);
  const [search, setSearch] = useState("");
  const [verb, setVerb] = useState<string>("All");
  const [error, setError] = useState("");

  const loadConnections = useCallback(async () => {
    setLoadingList(true);
    setError("");
    try {
      const list = await appApi.connections();
      setConnections(list);
      setSelected((prev) => (prev && list.some((c) => c.provider === prev) ? prev : (list[0]?.provider ?? null)));
    } catch (e) {
      setError(err(e));
    } finally {
      setLoadingList(false);
    }
  }, []);

  useEffect(() => {
    void loadConnections();
  }, [loadConnections]);

  const loadTools = useCallback(async (slug: string) => {
    setLoadingTools(true);
    setError("");
    setBundle(null);
    try {
      setBundle(await appApi.tools(slug));
    } catch (e) {
      setError(err(e));
    } finally {
      setLoadingTools(false);
    }
  }, []);

  useEffect(() => {
    if (selected) void loadTools(selected);
    else setBundle(null);
  }, [selected, loadTools]);

  const grouped = useMemo(() => {
    const tools: ToolkitTool[] = bundle?.tools ?? [];
    const q = search.trim().toLowerCase();
    const filtered = tools.filter((t) => {
      const matchesVerb = verb === "All" || actionOf(t.slug) === verb;
      const matchesQ =
        !q ||
        t.slug.toLowerCase().includes(q) ||
        t.name.toLowerCase().includes(q) ||
        t.description.toLowerCase().includes(q);
      return matchesVerb && matchesQ;
    });
    const verbs = new Set(tools.map((t) => actionOf(t.slug)));
    return {
      verbs: ACTION_ORDER.filter((a) => verbs.has(a)),
      filtered,
    };
  }, [bundle, search, verb]);

  const current = connections?.find((c) => c.provider === selected) ?? null;

  return (
    <Card
      title="Workspace"
      hint="One interface for every connected app. Pick an app, see exactly what PA can do in it."
      actions={
        <button type="button" className="btn btn-sm" onClick={() => void loadConnections()} disabled={loadingList}>
          Refresh
        </button>
      }
    >
      {error ? <ErrorBox message={error} onRetry={() => void loadConnections()} /> : null}

      {loadingList && !connections ? <Skeleton rows={3} /> : null}

      {connections && connections.length === 0 ? (
        <EmptyState
          title="No apps connected yet"
          hint="Connect an app to unlock its tools here."
        />
      ) : null}

      {connections && connections.length > 0 ? (
        <>
          <div className="tabs" role="tablist" aria-label="Connected apps">
            {connections.map((c) => (
              <button
                key={c.provider}
                role="tab"
                type="button"
                aria-selected={selected === c.provider}
                className="tab"
                onClick={() => setSelected(c.provider)}
              >
                {c.name}
                {c.state === "active" ? <span className="tab-dot" aria-hidden="true" /> : null}
              </button>
            ))}
            <button type="button" className="tab tab-more" onClick={onGoToApps}>
              + Connect more
            </button>
          </div>

          {current ? (
            <div className="ws-head">
              <div>
                <div className="ws-title">{current.name}</div>
                <div className="small muted">
                  {bundle ? `${bundle.tool_count} tools available` : "Loading tools…"}
                  {bundle && !bundle.connected ? " · connect this app to let PA use them" : ""}
                </div>
              </div>
              <Badge value={current.state} dot />
            </div>
          ) : null}

          {loadingTools ? <Skeleton rows={4} /> : null}

          {bundle && !bundle.connected ? (
            <div className="alert alert-info" role="status">
              {current?.name} isn&apos;t connected yet, so PA cannot run these tools. Use{" "}
              <strong>Connect more</strong> to finish linking it.
            </div>
          ) : null}

          {bundle && bundle.tools.length > 0 ? (
            <>
              <div className="ws-controls">
                <input
                  type="search"
                  placeholder="Search tools…"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  aria-label="Search tools"
                />
                <div className="chips">
                  {["All", ...grouped.verbs].map((v) => (
                    <button
                      key={v}
                      type="button"
                      className="chip"
                      aria-pressed={verb === v}
                      onClick={() => setVerb(v)}
                    >
                      {v}
                    </button>
                  ))}
                </div>
              </div>

              {grouped.filtered.length === 0 ? (
                <EmptyState title="No tools match" hint="Try a different search or action." />
              ) : (
                <ul className="ws-tools">
                  {grouped.filtered.map((t) => (
                    <li key={t.slug} className="ws-tool">
                      <div className="ws-tool-body">
                        <div className="ws-tool-name">{t.name}</div>
                        <div className="mono tiny muted">{t.slug}</div>
                        {t.description ? <p className="small ws-tool-desc">{t.description}</p> : null}
                      </div>
                      <button
                        type="button"
                        className="btn btn-sm"
                        onClick={() =>
                          onAskAssistant(
                            `Using the ${current?.name} app, ${t.name} (${t.slug}).`
                          )
                        }
                      >
                        Ask PA
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </>
          ) : null}

          {bundle && bundle.tools.length === 0 ? (
            <EmptyState title="No tools" hint="This toolkit exposes no tools yet." />
          ) : null}
        </>
      ) : null}
    </Card>
  );
}