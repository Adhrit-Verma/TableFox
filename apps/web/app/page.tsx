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
  WifiOff,
  X,
  ZoomIn,
  ZoomOut
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
  parent_id: string | null;
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

const API_URL = process.env.NEXT_PUBLIC_DBMAP_API_URL ?? "/dbmap-api";

const visibleKinds = new Set(["schema", "table", "view", "materialized_view"]);

function graphSignature(snapshot: GraphSnapshot) {
  return `${snapshot.nodes.map((node) => node.id).join("|")}::${snapshot.edges.map((edge) => edge.id).join("|")}`;
}

export default function Home() {
  const cyRef = useRef<Core | null>(null);
  const graphEl = useRef<HTMLDivElement | null>(null);
  const searchEl = useRef<HTMLDivElement | null>(null);
  const searchAbortRef = useRef<AbortController | null>(null);
  const nodeAbortRef = useRef<AbortController | null>(null);
  const graphSignatureRef = useRef("");
  const focusRequestedRef = useRef(false);
  const [graph, setGraph] = useState<GraphSnapshot | null>(null);
  const [selected, setSelected] = useState<NodeExplanation | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchLoading, setSearchLoading] = useState(false);
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

  const selectedRelationId = useMemo(() => {
    if (!selectedId || !graph) return null;
    const node = graph.nodes.find((item) => item.id === selectedId);
    if (!node) return null;
    if (["table", "view", "materialized_view"].includes(node.kind)) return node.id;
    const parent = graph.nodes.find((item) => item.id === node.parent_id);
    return parent && ["table", "view", "materialized_view"].includes(parent.kind) ? parent.id : null;
  }, [graph, selectedId]);

  const columnParentId = showColumns ? selectedRelationId : null;
  const visibleColumnCount = useMemo(
    () => graph?.nodes.filter((node) => node.kind === "column" && node.parent_id === columnParentId).length ?? 0,
    [columnParentId, graph]
  );

  const visibleGraph = useMemo(() => {
    if (!graph) return { nodes: [], edges: [] };
    const schemaFilterActive = activeSchemas.size > 0;
    const nodes = graph.nodes.filter((node) => {
      const isVisibleObject = visibleKinds.has(node.kind);
      const isSelectedColumn = node.kind === "column" && node.parent_id === columnParentId;
      if (!isVisibleObject && !isSelectedColumn) return false;
      if (!schemaFilterActive) return true;
      return node.kind === "schema" || (node.schema ? activeSchemas.has(node.schema) : true);
    });
    const nodeIds = new Set(nodes.map((node) => node.id));
    const edges = graph.edges.filter((edge) => {
      if (!nodeIds.has(edge.source) || !nodeIds.has(edge.target)) return false;
      if (edge.kind === "foreign_key" || edge.kind === "contains") return true;
      return Boolean(columnParentId) && edge.kind === "has_column";
    });
    return { nodes, edges };
  }, [activeSchemas, columnParentId, graph]);

  const applyGraph = useCallback((payload: GraphSnapshot, force = false) => {
    const signature = graphSignature(payload);
    if (!force && signature === graphSignatureRef.current) return;
    graphSignatureRef.current = signature;
    setGraph(payload);
  }, []);

  const loadGraph = useCallback(async (refresh = false) => {
    setStatus("loading");
    setError(null);
    try {
      const response = await fetch(`${API_URL}/graph${refresh ? "?refresh=true" : ""}`);
      if (!response.ok) throw new Error(`Graph request failed with ${response.status}`);
      const payload = (await response.json()) as GraphSnapshot;
      applyGraph(payload, refresh);
      setStatus("ready");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to load graph.");
      setStatus("error");
    }
  }, [applyGraph]);

  const loadNode = useCallback(async (id: string, focus = false) => {
    nodeAbortRef.current?.abort();
    const controller = new AbortController();
    nodeAbortRef.current = controller;
    focusRequestedRef.current = focus;
    setSelectedId(id);
    try {
      const response = await fetch(`${API_URL}/graph/node/${encodeURIComponent(id)}`, {
        signal: controller.signal
      });
      if (!response.ok) throw new Error(`Node request failed with ${response.status}`);
      setSelected((await response.json()) as NodeExplanation);
    } catch (err) {
      if (controller.signal.aborted) return;
      setSelected({
        found: false,
        id,
        summary: err instanceof Error ? err.message : "Unable to load node."
      });
    }
  }, []);

  const clearSelection = useCallback(() => {
    nodeAbortRef.current?.abort();
    focusRequestedRef.current = false;
    setShowColumns(false);
    setSelectedId(null);
    setSelected(null);
  }, []);

  useEffect(() => {
    loadGraph();
  }, [loadGraph]);

  useEffect(() => {
    if (!searchQuery.trim()) {
      searchAbortRef.current?.abort();
      setSearchResults([]);
      setSearchLoading(false);
      return;
    }
    setSearchLoading(true);
    searchAbortRef.current?.abort();
    const controller = new AbortController();
    searchAbortRef.current = controller;
    const timer = window.setTimeout(async () => {
      try {
        const response = await fetch(`${API_URL}/graph/search?q=${encodeURIComponent(searchQuery)}&limit=12`, {
          signal: controller.signal
        });
        if (response.ok) {
          const payload = (await response.json()) as { results: SearchResult[] };
          setSearchResults(payload.results);
        }
      } catch {
        if (!controller.signal.aborted) setSearchResults([]);
      } finally {
        if (!controller.signal.aborted) setSearchLoading(false);
      }
    }, 250);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [searchQuery]);

  useEffect(() => {
    const closeSearch = (event: PointerEvent) => {
      if (searchEl.current && !searchEl.current.contains(event.target as Node)) setSearchOpen(false);
    };
    document.addEventListener("pointerdown", closeSearch);
    return () => document.removeEventListener("pointerdown", closeSearch);
  }, []);

  useEffect(() => {
    const wsUrl = API_URL.replace(/^http/, "ws");
    let active = true;
    let socket: WebSocket | null = null;
    let reconnectTimer: number | null = null;

    const connect = () => {
      if (!active) return;
      setLive("connecting");
      socket = new WebSocket(`${wsUrl}/graph/live`);
      socket.onopen = () => setLive("connected");
      socket.onerror = () => socket?.close();
      socket.onclose = () => {
        setLive("offline");
        if (active) reconnectTimer = window.setTimeout(connect, 2000);
      };
      socket.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data) as {
            type: string;
            graph?: GraphSnapshot;
            error?: string;
          };
          if (payload.type === "graph_snapshot" && payload.graph) {
            applyGraph(payload.graph);
            setError(null);
            setStatus("ready");
          } else if (payload.type === "graph_error") {
            setError(payload.error ?? "Live graph refresh failed.");
          }
        } catch {
          setError("The live graph service returned an invalid update.");
        }
      };
    };

    connect();
    return () => {
      active = false;
      if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, [applyGraph]);

  useEffect(() => () => {
    searchAbortRef.current?.abort();
    nodeAbortRef.current?.abort();
    cyRef.current?.destroy();
    cyRef.current = null;
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
          parent: visibleKinds.has(node.kind) && node.kind !== "schema" && visibleGraph.nodes.some((item) => item.id === node.parent_id)
            ? node.parent_id ?? undefined
            : undefined
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
        boxSelectionEnabled: false,
        hideEdgesOnViewport: true,
        pixelRatio: 1,
        wheelSensitivity: 0.42,
        minZoom: 0.12,
        maxZoom: 3,
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
              "background-color": "oklch(0.92 0.04 205)",
              "border-color": "oklch(0.42 0.12 205)",
              "border-width": 1.5,
              color: "oklch(0.18 0.018 40)",
              height: 26,
              width: 104,
              "font-size": 9,
              shape: "round-rectangle",
              "text-margin-y": 0,
              "text-valign": "center"
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
            selector: "edge.has_column",
            style: {
              "line-color": "oklch(0.42 0.12 205)",
              "line-style": "dashed",
              opacity: 0.85,
              "target-arrow-shape": "none",
              width: 1.4
            }
          },
          {
            selector: ":selected",
            style: {
              "border-color": "oklch(0.18 0.018 40)",
              "border-width": 3
            }
          },
          {
            selector: ".faded",
            style: {
              opacity: 0.13,
              "text-opacity": 0.08
            }
          },
          {
            selector: ".connected",
            style: {
              opacity: 1,
              "z-index": 8
            }
          }
        ],
        layout: visibleGraph.nodes.length > 180
          ? { name: "grid", animate: false, avoidOverlap: true, spacingFactor: 1.15 }
          : { name: "cose", animate: false, idealEdgeLength: 120, nodeRepulsion: 7200, numIter: 600 }
      });
      cyRef.current.on("tap", "node", (event: EventObject) => loadNode(event.target.id()));
      cyRef.current.on("tap", (event: EventObject) => {
        if (event.target === cyRef.current) clearSelection();
      });
    } else {
      cyRef.current.batch(() => {
        cyRef.current?.elements().remove();
        cyRef.current?.add(elements);
      });
      const layout = visibleGraph.nodes.length > 180
        ? { name: "grid", animate: false, avoidOverlap: true, spacingFactor: 1.15 }
        : { name: "cose", animate: false, idealEdgeLength: 120, nodeRepulsion: 7200, numIter: 600 };
      cyRef.current.layout(layout).run();
    }
  }, [clearSelection, graph, loadNode, visibleGraph]);

  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.batch(() => {
      cy.elements().removeClass("faded connected").unselect();
      if (!selectedId) return;
      const node = cy.getElementById(selectedId);
      if (!node.length) return;
      const neighborhood = node.closedNeighborhood();
      cy.elements().difference(neighborhood).addClass("faded");
      neighborhood.addClass("connected");
      node.select();
    });
    if (selectedId && focusRequestedRef.current) {
      const node = cy.getElementById(selectedId);
      if (node.length) cy.animate({ center: { eles: node }, zoom: 1.2 }, { duration: 160 });
      focusRequestedRef.current = false;
    }
  }, [selectedId, visibleGraph]);

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

  const zoomBy = (factor: number) => {
    const cy = cyRef.current;
    if (!cy) return;
    const nextZoom = Math.min(cy.maxZoom(), Math.max(cy.minZoom(), cy.zoom() * factor));
    cy.animate({ zoom: nextZoom }, { duration: 140 });
  };

  const fitGraph = () => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.animate({ fit: { eles: cy.elements(), padding: 42 } }, { duration: 180 });
  };

  const clearSearch = () => {
    searchAbortRef.current?.abort();
    setSearchQuery("");
    setSearchResults([]);
    setSearchOpen(false);
    setSearchLoading(false);
  };

  const selectSearchResult = (result: SearchResult) => {
    clearSearch();
    loadNode(result.id, true);
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

        <div className="command" ref={searchEl}>
          <Search aria-hidden="true" size={16} />
          <input
            aria-label="Search database graph"
            value={searchQuery}
            onChange={(event) => {
              setSearchQuery(event.target.value);
              setSearchOpen(true);
            }}
            onFocus={() => {
              if (searchQuery.trim()) setSearchOpen(true);
            }}
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                event.preventDefault();
                clearSearch();
              }
            }}
            placeholder="Search tables, columns, comments, constraints"
            aria-expanded={searchOpen && Boolean(searchQuery.trim())}
            aria-controls="database-search-results"
          />
          {searchQuery && (
            <button className="searchClear" onClick={clearSearch} aria-label="Clear search" title="Clear search">
              <X size={15} />
            </button>
          )}
          {searchOpen && searchQuery.trim() && (
            <div className="searchResults" id="database-search-results" aria-label="Database search results">
              <div className="searchResultsHeader">
                <span>{searchLoading ? "Searching" : `${searchResults.length} results`}</span>
                <button onClick={() => setSearchOpen(false)} aria-label="Close search results" title="Close results">
                  <X size={14} />
                </button>
              </div>
              <div className="searchResultsList">
                {!searchLoading && searchResults.length === 0 && (
                  <p className="searchEmpty">No matching database objects.</p>
                )}
                {searchResults.map((result) => (
                  <button key={result.id} onClick={() => selectSearchResult(result)}>
                    <span>{result.label}</span>
                    <small>
                      {result.kind}
                      {result.parent_id
                        ? ` / ${result.parent_id.split(":").slice(1).join(":")}`
                        : result.schema ? ` / ${result.schema}` : ""}
                    </small>
                  </button>
                ))}
              </div>
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
            <input
              type="checkbox"
              checked={showColumns}
              disabled={!selectedRelationId}
              onChange={(event) => {
                focusRequestedRef.current = event.target.checked;
                setShowColumns(event.target.checked);
              }}
            />
            <span>Show columns</span>
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
        <div className="canvasMeta" aria-live="polite">
          <span>{visibleGraph.nodes.length.toLocaleString()} nodes</span>
          <span>{visibleGraph.edges.length.toLocaleString()} links</span>
          {columnParentId && <span>{visibleColumnCount.toLocaleString()} columns</span>}
        </div>
        <div className="zoomControls" aria-label="Graph zoom controls">
          <button onClick={() => zoomBy(1.25)} aria-label="Zoom in" title="Zoom in">
            <ZoomIn size={17} />
          </button>
          <button onClick={() => zoomBy(0.8)} aria-label="Zoom out" title="Zoom out">
            <ZoomOut size={17} />
          </button>
          <button onClick={fitGraph} aria-label="Fit graph" title="Fit graph">
            <Maximize2 size={17} />
          </button>
        </div>
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
              <div className="detailHeader">
                <span className="kindPill">{selected.kind ?? "object"}</span>
                <button className="iconButton" onClick={clearSelection} aria-label="Close object details" title="Close details">
                  <X size={15} />
                </button>
              </div>
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
              <RelationshipList title="References" items={selected.foreign_keys_to ?? []} onOpen={(id) => loadNode(id, true)} />
              <RelationshipList title="Referenced by" items={selected.referenced_by ?? []} onOpen={(id) => loadNode(id, true)} />
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
