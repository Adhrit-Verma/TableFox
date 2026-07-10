"use client";

import cytoscape, { Core, ElementDefinition, EventObject } from "cytoscape";
import {
  Activity,
  AlertTriangle,
  Columns3,
  Database,
  Eye,
  Filter,
  GitBranch,
  Loader2,
  Maximize2,
  RefreshCw,
  Search,
  Table2,
  Wifi,
  WifiOff
} from "lucide-react";
import type { ReactNode } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

type GraphNode = {
  id: string;
  kind: string;
  label: string;
  schema: string | null;
  name: string | null;
  parent_id: string | null;
  metadata: Record<string, unknown>;
};

type GraphEdge = {
  id: string;
  kind: string;
  source: string;
  target: string;
  label: string | null;
  metadata: Record<string, unknown>;
};

type GraphSnapshot = {
  database: string;
  generated_at: string;
  summary: Record<string, number>;
  nodes: GraphNode[];
  edges: GraphEdge[];
};

type SearchResult = {
  id: string;
  kind: string;
  label: string;
  schema: string | null;
  metadata: Record<string, unknown>;
};

type NodeExplanation = {
  found: boolean;
  id: string;
  kind?: string;
  label?: string;
  schema?: string | null;
  summary?: string;
  metadata?: Record<string, unknown>;
  columns?: Array<{
    id: string;
    name: string;
    data_type: string | null;
    is_nullable: string | null;
    comment: string | null;
  }>;
  foreign_keys_to?: Array<{ id: string; label: string }>;
  referenced_by?: Array<{ id: string; label: string }>;
};

const API_URL = process.env.NEXT_PUBLIC_DBMAP_API_URL ?? "http://127.0.0.1:8000";

const visibleKinds = new Set(["schema", "table", "view", "materialized_view"]);

export default function Home() {
  const cyRef = useRef<Core | null>(null);
  const graphEl = useRef<HTMLDivElement | null>(null);
  const [graph, setGraph] = useState<GraphSnapshot | null>(null);
  const [selected, setSelected] = useState<NodeExplanation | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
  const [activeSchemas, setActiveSchemas] = useState<Set<string>>(new Set());
  const [showColumns, setShowColumns] = useState(false);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [live, setLive] = useState<"connecting" | "connected" | "offline">("connecting");
  const [error, setError] = useState<string | null>(null);

  const schemas = useMemo(() => {
    if (!graph) return [];
    return graph.nodes
      .filter((node) => node.kind === "schema")
      .map((node) => node.label)
      .sort((a, b) => a.localeCompare(b));
  }, [graph]);

  const visibleGraph = useMemo(() => {
    if (!graph) return { nodes: [], edges: [] };
    const schemaFilterActive = activeSchemas.size > 0;
    const nodes = graph.nodes.filter((node) => {
      if (!showColumns && !visibleKinds.has(node.kind)) return false;
      if (!schemaFilterActive) return true;
      return node.kind === "schema" || (node.schema ? activeSchemas.has(node.schema) : true);
    });
    const nodeIds = new Set(nodes.map((node) => node.id));
    const edges = graph.edges.filter((edge) => {
      if (!nodeIds.has(edge.source) || !nodeIds.has(edge.target)) return false;
      if (edge.kind === "foreign_key" || edge.kind === "contains") return true;
      return showColumns && ["has_column", "has_constraint", "has_index"].includes(edge.kind);
    });
    return { nodes, edges };
  }, [activeSchemas, graph, showColumns]);

  const loadGraph = useCallback(async (refresh = false) => {
    setStatus("loading");
    setError(null);
    try {
      const response = await fetch(`${API_URL}/graph${refresh ? "?refresh=true" : ""}`);
      if (!response.ok) throw new Error(`Graph request failed with ${response.status}`);
      const payload = (await response.json()) as GraphSnapshot;
      setGraph(payload);
      setStatus("ready");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to load graph.");
      setStatus("error");
    }
  }, []);

  const loadNode = useCallback(async (id: string) => {
    setSelectedId(id);
    try {
      const response = await fetch(`${API_URL}/graph/node/${encodeURIComponent(id)}`);
      if (!response.ok) throw new Error(`Node request failed with ${response.status}`);
      setSelected((await response.json()) as NodeExplanation);
    } catch (err) {
      setSelected({
        found: false,
        id,
        summary: err instanceof Error ? err.message : "Unable to load node."
      });
    }
  }, []);

  useEffect(() => {
    loadGraph();
  }, [loadGraph]);

  useEffect(() => {
    if (!searchQuery.trim()) {
      setSearchResults([]);
      return;
    }
    const timer = window.setTimeout(async () => {
      const response = await fetch(`${API_URL}/graph/search?q=${encodeURIComponent(searchQuery)}&limit=12`);
      if (response.ok) {
        const payload = (await response.json()) as { results: SearchResult[] };
        setSearchResults(payload.results);
      }
    }, 180);
    return () => window.clearTimeout(timer);
  }, [searchQuery]);

  useEffect(() => {
    const wsUrl = API_URL.replace(/^http/, "ws");
    const socket = new WebSocket(`${wsUrl}/graph/live`);
    setLive("connecting");
    socket.onopen = () => setLive("connected");
    socket.onclose = () => setLive("offline");
    socket.onerror = () => setLive("offline");
    socket.onmessage = (event) => {
      const payload = JSON.parse(event.data) as { type: string; graph?: GraphSnapshot };
      if (payload.type === "graph_snapshot" && payload.graph) {
        setGraph(payload.graph);
        setStatus("ready");
      }
    };
    return () => socket.close();
  }, []);

  useEffect(() => {
    if (!graphEl.current || !graph) return;

    const elements: ElementDefinition[] = [
      ...visibleGraph.nodes.map((node) => ({
        data: {
          id: node.id,
          label: node.kind === "schema" ? node.label : node.name ?? node.label,
          kind: node.kind,
          schema: node.schema ?? "",
          parent: node.kind !== "schema" && visibleGraph.nodes.some((item) => item.id === node.parent_id) ? node.parent_id ?? undefined : undefined
        },
        classes: node.kind
      })),
      ...visibleGraph.edges.map((edge) => ({
        data: {
          id: edge.id,
          source: edge.source,
          target: edge.target,
          label: edge.label ?? edge.kind,
          kind: edge.kind
        },
        classes: edge.kind
      }))
    ];

    if (!cyRef.current) {
      cyRef.current = cytoscape({
        container: graphEl.current,
        elements,
        wheelSensitivity: 0.18,
        minZoom: 0.12,
        maxZoom: 2.2,
        style: [
          {
            selector: "node",
            style: {
              "background-color": "oklch(0.5 0.151 40)",
              "border-color": "oklch(0.35 0.12 40)",
              "border-width": 1,
              color: "oklch(0.18 0.018 40)",
              "font-family": "Inter, Segoe UI, system-ui",
              "font-size": 11,
              height: 34,
              label: "data(label)",
              "text-margin-y": 8,
              "text-valign": "bottom",
              width: 34
            }
          },
          {
            selector: "node.schema",
            style: {
              "background-opacity": 0.05,
              "background-color": "oklch(0.42 0.12 205)",
              "border-color": "oklch(0.78 0.035 205)",
              "border-style": "solid",
              "border-width": 1,
              "font-size": 12,
              "font-weight": 700,
              padding: "22px",
              shape: "round-rectangle",
              "text-valign": "top"
            }
          },
          {
            selector: "node.table",
            style: {
              "background-color": "oklch(0.5 0.151 40)",
              shape: "round-rectangle",
              height: 38,
              width: 88
            }
          },
          {
            selector: "node.view, node.materialized_view",
            style: {
              "background-color": "oklch(0.42 0.12 205)",
              shape: "round-tag",
              height: 34,
              width: 86
            }
          },
          {
            selector: "node.column",
            style: {
              "background-color": "oklch(0.72 0.045 40)",
              height: 18,
              width: 18,
              "font-size": 9
            }
          },
          {
            selector: "edge",
            style: {
              "curve-style": "bezier",
              "line-color": "oklch(0.72 0.02 40)",
              opacity: 0.72,
              "target-arrow-color": "oklch(0.72 0.02 40)",
              "target-arrow-shape": "triangle",
              width: 1.2
            }
          },
          {
            selector: "edge.foreign_key",
            style: {
              "line-color": "oklch(0.42 0.12 205)",
              "target-arrow-color": "oklch(0.42 0.12 205)",
              width: 2.2
            }
          },
          {
            selector: ":selected",
            style: {
              "border-color": "oklch(0.18 0.018 40)",
              "border-width": 3
            }
          }
        ],
        layout: { name: "cose", animate: false, idealEdgeLength: 140, nodeRepulsion: 9000 }
      });
      cyRef.current.on("tap", "node", (event: EventObject) => loadNode(event.target.id()));
    } else {
      cyRef.current.elements().remove();
      cyRef.current.add(elements);
      cyRef.current.layout({ name: "cose", animate: false, idealEdgeLength: 140, nodeRepulsion: 9000 }).run();
    }

    if (selectedId) {
      cyRef.current.getElementById(selectedId).select();
    }
  }, [graph, loadNode, selectedId, visibleGraph]);

  const toggleSchema = (schema: string) => {
    setActiveSchemas((current) => {
      const next = new Set(current);
      if (next.has(schema)) next.delete(schema);
      else next.add(schema);
      return next;
    });
  };

  const focusSelected = () => {
    if (!cyRef.current || !selectedId) return;
    const node = cyRef.current.getElementById(selectedId);
    if (node.length) cyRef.current.animate({ center: { eles: node }, zoom: 1.15 }, { duration: 180 });
  };

  const fitGraph = () => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.animate({ fit: { eles: cy.elements(), padding: 42 } }, { duration: 180 });
  };

  return (
    <main className="shell">
      <header className="topbar">
        <div className="brand">
          <Database aria-hidden="true" size={21} />
          <div>
            <strong>Database Agent</strong>
            <span>{graph?.database ?? "PostgreSQL graph map"}</span>
          </div>
        </div>

        <div className="command">
          <Search aria-hidden="true" size={16} />
          <input
            aria-label="Search database graph"
            value={searchQuery}
            onChange={(event) => setSearchQuery(event.target.value)}
            placeholder="Search tables, columns, comments, constraints"
          />
          {searchResults.length > 0 && (
            <div className="searchResults">
              {searchResults.map((result) => (
                <button key={result.id} onClick={() => loadNode(result.id)}>
                  <span>{result.label}</span>
                  <small>{result.kind}{result.schema ? ` / ${result.schema}` : ""}</small>
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="statusCluster" aria-live="polite">
          <span className={`live live-${live}`}>
            {live === "connected" ? <Wifi size={14} /> : <WifiOff size={14} />}
            {live}
          </span>
          <span className={`state state-${status}`}>
            {status === "loading" ? <Loader2 className="spin" size={14} /> : <Activity size={14} />}
            {status}
          </span>
          <button className="iconButton" onClick={() => loadGraph(true)} aria-label="Refresh graph">
            <RefreshCw size={16} />
          </button>
        </div>
      </header>

      <aside className="rail" aria-label="Graph filters">
        <section>
          <h2><Filter size={15} /> Schemas</h2>
          <div className="schemaList">
            {schemas.length === 0 && <p className="emptyText">No schema metadata loaded.</p>}
            {schemas.map((schema) => (
              <label key={schema} className="checkRow">
                <input
                  type="checkbox"
                  checked={activeSchemas.has(schema)}
                  onChange={() => toggleSchema(schema)}
                />
                <span>{schema}</span>
              </label>
            ))}
          </div>
        </section>

        <section>
          <h2><Eye size={15} /> View</h2>
          <label className="switchRow">
            <input type="checkbox" checked={showColumns} onChange={(event) => setShowColumns(event.target.checked)} />
            <span>Show column nodes</span>
          </label>
          <button className="wideButton" onClick={fitGraph}>
            <Maximize2 size={15} />
            Fit graph
          </button>
          <button className="wideButton" onClick={focusSelected} disabled={!selectedId}>
            <GitBranch size={15} />
            Focus selection
          </button>
        </section>

        <section className="metrics">
          <h2><Activity size={15} /> Snapshot</h2>
          <Metric label="Tables" value={graph?.summary.table ?? 0} />
          <Metric label="Views" value={(graph?.summary.view ?? 0) + (graph?.summary.materialized_view ?? 0)} />
          <Metric label="Columns" value={graph?.summary.column ?? 0} />
          <Metric label="Edges" value={graph?.summary.edges ?? 0} />
        </section>
      </aside>

      <section className="canvasPane" aria-label="Database graph canvas">
        {error && (
          <div className="errorBanner" role="alert">
            <AlertTriangle size={16} />
            <span>{error}</span>
          </div>
        )}
        <div ref={graphEl} className="graphCanvas" />
      </section>

      <aside className="inspector" aria-label="Selected graph object">
        {!selected && (
          <div className="emptyInspector">
            <Table2 size={26} />
            <h2>Select a table or view</h2>
            <p>Open a node to inspect columns, constraints, and relationship direction.</p>
          </div>
        )}
        {selected && (
          <div className="detailStack">
            <div>
              <span className="kindPill">{selected.kind ?? "object"}</span>
              <h1>{selected.label ?? selected.id}</h1>
              <p>{selected.summary ?? "No object details are available for this ID."}</p>
            </div>

            <InspectorGroup title="Columns" icon={<Columns3 size={15} />}>
              {selected.columns?.length ? (
                <div className="columnList">
                  {selected.columns.map((column) => (
                    <div key={column.id} className="columnRow">
                      <strong>{column.name}</strong>
                      <span>{column.data_type}</span>
                      <small>{column.is_nullable === "NO" ? "required" : "nullable"}</small>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="emptyText">No column details for this object.</p>
              )}
            </InspectorGroup>

            <InspectorGroup title="Relationships" icon={<GitBranch size={15} />}>
              <RelationshipList title="References" items={selected.foreign_keys_to ?? []} onOpen={loadNode} />
              <RelationshipList title="Referenced by" items={selected.referenced_by ?? []} onOpen={loadNode} />
            </InspectorGroup>
          </div>
        )}
      </aside>
    </main>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{value.toLocaleString()}</strong>
    </div>
  );
}

function InspectorGroup({ title, icon, children }: { title: string; icon: ReactNode; children: ReactNode }) {
  return (
    <section className="inspectorGroup">
      <h2>{icon}{title}</h2>
      {children}
    </section>
  );
}

function RelationshipList({
  title,
  items,
  onOpen
}: {
  title: string;
  items: Array<{ id: string; label: string }>;
  onOpen: (id: string) => void;
}) {
  return (
    <div className="relationshipBlock">
      <h3>{title}</h3>
      {items.length ? (
        items.map((item) => (
          <button key={item.id} onClick={() => onOpen(item.id)}>
            {item.label}
          </button>
        ))
      ) : (
        <p className="emptyText">None detected.</p>
      )}
    </div>
  );
}
