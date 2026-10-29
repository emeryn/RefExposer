import { ActionIcon, Badge, Box, Button, Group, Slider, Stack, Text, Tooltip } from '@mantine/core';
import {
  IconArrowsMaximize,
  IconEye,
  IconEyeOff,
  IconFocusCentered,
  IconHandStop,
  IconMaximize,
  IconMinimize,
  IconUnlink,
  IconZoomIn,
  IconZoomOut,
} from '@tabler/icons-react';
import { type PointerEvent as Pointer, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { GraphEdge, GraphNode } from '../api/types';

/**
 * The relations of an investigation, laid out and drawn by hand.
 *
 * A force-directed layout in a few dozen lines rather than a graph library: the project draws its own tables
 * and Markdown, the graphs here are small by construction (the walk is bounded), and a dependency of that
 * size would have to be proxied through an internal registry and followed for advisories.
 *
 * The layout is deterministic — the seed of the pseudo random placement is the identifier of each node — so
 * the same investigation always draws the same picture, and the same node keeps its place when the graph is
 * replayed.
 *
 * What the picture *shows* — how far apart, which kinds are hidden, which relations were set aside — is not
 * held here but passed in: the same graph is drawn twice, in the page and in the enlarged window, and work
 * done on one of them has to survive moving to the other.
 */

/** One colour per kind of identifier, plus one for a referential. Mantine tokens, so dark mode follows. */
const COLORS: Record<string, string> = {
  source: 'var(--mantine-color-gray-6)',
  query: 'var(--mantine-color-dark-6)',
  cve: 'var(--mantine-color-red-6)',
  cwe: 'var(--mantine-color-orange-6)',
  capec: 'var(--mantine-color-orange-8)',
  attack_technique: 'var(--mantine-color-grape-6)',
  cpe: 'var(--mantine-color-violet-6)',
  ip: 'var(--mantine-color-blue-6)',
  cidr: 'var(--mantine-color-indigo-6)',
  domain: 'var(--mantine-color-teal-6)',
  url: 'var(--mantine-color-cyan-7)',
  email: 'var(--mantine-color-pink-6)',
  sha256: 'var(--mantine-color-lime-7)',
  sha1: 'var(--mantine-color-lime-8)',
  md5: 'var(--mantine-color-yellow-7)',
  asn: 'var(--mantine-color-blue-9)',
  mac: 'var(--mantine-color-dark-3)',
};

const color = (node: GraphNode) =>
  node.kind === 'entity'
    ? (COLORS[node.entity ?? ''] ?? 'var(--mantine-color-dark-4)')
    : (COLORS[node.kind] ?? COLORS.source);

/**
 * One ring colour per seed, so with several values you can see which one reached what. A node reached from
 * more than one seed is the interesting case and gets a double ring instead of a colour of its own.
 */
const SEED_RINGS = [
  'var(--mantine-color-red-6)',
  'var(--mantine-color-blue-6)',
  'var(--mantine-color-teal-6)',
  'var(--mantine-color-orange-6)',
  'var(--mantine-color-grape-6)',
  'var(--mantine-color-lime-7)',
  'var(--mantine-color-cyan-7)',
  'var(--mantine-color-pink-6)',
];

/** The key a node is filtered and legended by: its entity type, or what it is when it is not a value. */
export const nodeKind = (node: GraphNode): string => (node.kind === 'entity' ? (node.entity ?? 'other') : node.kind);

/**
 * How one relation is named, here and in the page that remembers which ones were set aside.
 *
 * The same five fields as the merge of an expansion, and for the same reason: two values can share a row of
 * one referential and another row of another one, and those are two relations, not one.
 */
export const edgeKey = (edge: GraphEdge): string =>
  `${edge.source}|${edge.target}|${edge.kind}|${edge.referential_id}|${edge.label}`;

const seedRing = (node: GraphNode, several: boolean): string | null => {
  if (!several) return null;
  const seeds = node.seeds ?? [];
  if (seeds.length > 1) return 'var(--mantine-color-yellow-5)'; // a meeting point
  if (seeds.length === 1) return SEED_RINGS[seeds[0] % SEED_RINGS.length];
  return null;
};

/** Radius: the seed stands out, a referential is a square-ish hub, the rest scales a little with its rows. */
const radius = (node: GraphNode) => {
  if (node.seed || node.kind === 'query') return 13;
  if (node.kind === 'source') return 10;
  return node.rows > 4 ? 8 : 6;
};

interface Placed extends GraphNode {
  x: number;
  y: number;
  vx: number;
  vy: number;
}

/** Deterministic [0, 1) from a string, so a layout never moves between two runs. */
function hashed(text: string): number {
  let h = 2166136261;
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return ((h >>> 0) % 100000) / 100000;
}

const WIDTH = 900;
const HEIGHT = 520;

/**
 * Iterations of the simulation. It is O(nodes²) per iteration, so a big graph gets fewer passes: the layout
 * of a crowded graph is decided by the repulsion long before the 320th one, and the spacing control has to
 * stay answerable while it is dragged.
 */
const iterations = (count: number) => (count > 220 ? 140 : count > 120 ? 220 : 320);

/** Above this many nodes the spacing is applied when the slider is released rather than while dragging. */
const LIVE_LAYOUT_MAX = 150;

/**
 * Where a seed is pinned. One seed sits in the middle; several are spread evenly around it, starting on the
 * left, so two values read left and right with whatever ties them together in between.
 *
 * Pinning the seeds is what makes a picture stable — the question stays where you left it — but they have to
 * be pinned to *different* places, or several seeds stack on one point and nothing can be read.
 */
function seedAnchor(index: number, count: number, spacing: number): { x: number; y: number } {
  if (count <= 1) return { x: WIDTH / 2, y: HEIGHT / 2 };
  const angle = Math.PI + (2 * Math.PI * index) / count;
  const radius = Math.min(WIDTH, HEIGHT) * 0.32 * spacing;
  return { x: WIDTH / 2 + Math.cos(angle) * radius, y: HEIGHT / 2 + Math.sin(angle) * radius };
}

function layout(nodes: GraphNode[], edges: GraphEdge[], spacing = 1, seedCount = 1): Placed[] {
  if (!nodes.length) return [];
  const placed: Placed[] = nodes.map((n) => {
    if (n.seed) {
      const anchor = seedAnchor(n.seed_index ?? 0, seedCount, spacing);
      return { ...n, ...anchor, vx: 0, vy: 0 };
    }
    // Starting ring by depth, each hop further out. A node only one seed reached starts on that seed's side,
    // so the picture settles into one cluster per value with the shared ones between them.
    const own = n.seeds?.length === 1 ? n.seeds[0] : null;
    const spread = own === null ? Math.PI * 2 : Math.PI / Math.max(2, seedCount);
    const base = own === null ? 0 : Math.PI + (2 * Math.PI * own) / Math.max(1, seedCount);
    const angle = base + (hashed(n.id) - 0.5) * spread;
    const ring = (n.kind === 'source' ? 1.1 : n.depth + 1.4) * 70 * spacing;
    const from = own === null ? { x: WIDTH / 2, y: HEIGHT / 2 } : seedAnchor(own, seedCount, spacing);
    return { ...n, x: from.x + Math.cos(angle) * ring, y: from.y + Math.sin(angle) * ring, vx: 0, vy: 0 };
  });
  const index = new Map(placed.map((n, i) => [n.id, i]));
  const links = edges
    .map((e) => [index.get(e.source), index.get(e.target)] as [number | undefined, number | undefined])
    .filter((l): l is [number, number] => l[0] !== undefined && l[1] !== undefined && l[0] !== l[1]);
  // How many links each node has: a hub is held closer to the centre so the picture stays readable
  const degree = placed.map(() => 0);
  for (const [a, b] of links) {
    degree[a]++;
    degree[b]++;
  }

  const ITERATIONS = iterations(placed.length);
  // Spacing scales how hard nodes push each other apart and how long an edge wants to be; the pull back to
  // the centre is relaxed to match, or a wider graph would simply be squeezed into the same disc
  const repulsion = 2600 * spacing * spacing;
  const rest = 110 * spacing;
  const gather = 0.012 / spacing;
  for (let step = 0; step < ITERATIONS; step++) {
    const cooling = 1 - step / ITERATIONS;
    // Repulsion between every pair: small graphs, so the straightforward O(n²) pass is fine
    for (let i = 0; i < placed.length; i++) {
      for (let j = i + 1; j < placed.length; j++) {
        const a = placed[i];
        const b = placed[j];
        let dx = a.x - b.x;
        let dy = a.y - b.y;
        let dist = Math.hypot(dx, dy);
        if (dist < 0.01) {
          // Two nodes exactly on top of each other: push them apart along a fixed direction
          dx = hashed(a.id + b.id) - 0.5;
          dy = hashed(b.id + a.id) - 0.5;
          dist = 0.01;
        }
        const push = (repulsion / (dist * dist)) * cooling;
        const fx = (dx / dist) * push;
        const fy = (dy / dist) * push;
        a.vx += fx;
        a.vy += fy;
        b.vx -= fx;
        b.vy -= fy;
      }
    }
    // Attraction along the edges, weaker for a hub so it does not drag everything onto itself
    for (const [i, j] of links) {
      const a = placed[i];
      const b = placed[j];
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const dist = Math.max(Math.hypot(dx, dy), 0.01);
      const pull = ((dist - rest) * 0.012 * cooling) / Math.max(1, Math.log2(1 + Math.min(degree[i], degree[j])));
      a.vx += (dx / dist) * pull * dist * 0.02;
      a.vy += (dy / dist) * pull * dist * 0.02;
      b.vx -= (dx / dist) * pull * dist * 0.02;
      b.vy -= (dy / dist) * pull * dist * 0.02;
    }
    for (const node of placed) {
      // A seed stays put: it is the question, and fixed anchors make every picture readable the same way
      if (node.seed) {
        const anchor = seedAnchor(node.seed_index ?? 0, seedCount, spacing);
        node.x = anchor.x;
        node.y = anchor.y;
        node.vx = node.vy = 0;
        continue;
      }
      const toCentre = gather * cooling;
      node.vx += (WIDTH / 2 - node.x) * toCentre;
      node.vy += (HEIGHT / 2 - node.y) * toCentre;
      node.x += Math.max(-18, Math.min(18, node.vx));
      node.y += Math.max(-18, Math.min(18, node.vy));
      node.vx *= 0.82;
      node.vy *= 0.82;
    }
  }
  return placed;
}

/** The box the drawing actually occupies, so the view fits it whatever the layout settled on. */
function extent(placed: Placed[]): { x: number; y: number; w: number; h: number } {
  if (!placed.length) return { x: 0, y: 0, w: WIDTH, h: HEIGHT };
  const pad = 48;
  const xs = placed.map((n) => n.x);
  const ys = placed.map((n) => n.y);
  const x = Math.min(...xs) - pad;
  const y = Math.min(...ys) - pad;
  return { x, y, w: Math.max(...xs) + pad - x, h: Math.max(...ys) + pad - y };
}

function shorten(text: string, max = 22): string {
  return text.length <= max ? text : `${text.slice(0, max - 1)}…`;
}

/** How a kind reads in the legend and in the sentence under the graph. */
const kindLabel = (kind: string) =>
  kind === 'source' ? 'referential' : kind === 'query' ? 'text searched' : kind;

export function RelationGraph({
  nodes,
  edges,
  onSelect,
  selected,
  height = 520,
  seedCount = 1,
  fullscreen = false,
  onFullscreen,
  hidden,
  onHidden,
  removed,
  onRemoved,
  spacing,
  onSpacing,
}: {
  nodes: GraphNode[];
  edges: GraphEdge[];
  onSelect?: (node: GraphNode | null) => void;
  selected?: string | null;
  height?: number | string;
  /** Several seeds: nodes are then ringed by the seed that reached them */
  seedCount?: number;
  /** Drawn inside the enlarged window: the button then closes it */
  fullscreen?: boolean;
  /** Offered only when the page can enlarge the graph */
  onFullscreen?: (open: boolean) => void;
  /**
   * The kinds of element left out of the picture, and the relations set aside, held by the page rather than
   * here: enlarging the graph draws a second component, and a filter that reset on the way there would
   * undo the very work it was applied to. Hiding is not merely a display trick — what is hidden leaves the
   * layout too, so what remains actually spreads out.
   */
  hidden: Set<string>;
  onHidden: (next: Set<string>) => void;
  /** Relations set aside, by `edgeKey`. The evidence behind them stays in the Evidence tab. */
  removed: Set<string>;
  onRemoved: (next: Set<string>) => void;
  /** How far apart the simulation holds the nodes */
  spacing: number;
  onSpacing: (value: number) => void;
}) {
  // What the slider shows while it is being dragged: on a large graph the layout only follows on release
  const [dragging, setDragging] = useState(spacing);
  useEffect(() => setDragging(spacing), [spacing]);
  const several = seedCount > 1;
  /** The relation under consideration: clicking one offers to set it aside. */
  const [edge, setEdge] = useState<string | null>(null);

  /** Every kind present, with how many nodes it has: the legend, and the filter. */
  const kinds = useMemo(() => {
    const counts = new Map<string, number>();
    for (const n of nodes) counts.set(nodeKind(n), (counts.get(nodeKind(n)) ?? 0) + 1);
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [nodes]);
  const allHidden = kinds.length > 0 && kinds.every(([kind]) => hidden.has(kind));

  const shown = useMemo(() => {
    const keptEdges = removed.size ? edges.filter((e) => !removed.has(edgeKey(e))) : edges;
    if (!hidden.size) return { nodes, edges: keptEdges };
    // A seed is never hidden: it is the question, and a graph without it reads as someone else's
    const keep = nodes.filter((n) => n.seed || !hidden.has(nodeKind(n)));
    const ids = new Set(keep.map((n) => n.id));
    return { nodes: keep, edges: keptEdges.filter((e) => ids.has(e.source) && ids.has(e.target)) };
  }, [nodes, edges, hidden, removed]);

  // The layout is the expensive part and depends only on the graph and how far apart it is asked to hold it
  const placed = useMemo(() => layout(shown.nodes, shown.edges, spacing, seedCount), [shown, spacing, seedCount]);
  const box = useMemo(() => extent(placed), [placed]);
  const [zoom, setZoom] = useState(1);
  // Where the view is centred, in the units of the drawing. Zooming without being able to move is a
  // magnifying glass bolted to the table: the middle of a crowded graph is rarely what you want to read.
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [hovered, setHovered] = useState<string | null>(null);
  const svg = useRef<SVGSVGElement>(null);
  // A new graph is a new question: fit it again. Hiding a kind or setting a relation aside is not — the view
  // you arranged stays where you put it.
  useEffect(() => {
    setZoom(1);
    setPan({ x: 0, y: 0 });
    setEdge(null);
  }, [nodes, edges]);
  const live = nodes.length <= LIVE_LAYOUT_MAX;

  const byId = useMemo(() => new Map(placed.map((n) => [n.id, n])), [placed]);
  // Neighbours of whatever is hovered or selected: everything else is dimmed rather than hidden
  const focus = hovered ?? selected ?? null;
  const near = useMemo(() => {
    if (!focus) return null;
    const set = new Set<string>([focus]);
    for (const e of shown.edges) {
      if (e.source === focus) set.add(e.target);
      if (e.target === focus) set.add(e.source);
    }
    return set;
  }, [focus, shown.edges]);

  const fit = () => {
    setZoom(1);
    setPan({ x: 0, y: 0 });
  };

  /**
   * Dragging the picture. The movement is in screen pixels and the view in the units of the drawing, so it
   * is converted through the rendered size — and through the *uniform* scale the viewBox actually uses,
   * which is the larger of the two ratios, or the picture would slide away from the pointer.
   */
  const drag = useRef<{ x: number; y: number; px: number; py: number; moved: boolean } | null>(null);
  // A drag that ends on the background must not also read as a click, which clears the selection
  const dragged = useRef(false);

  const scale = useCallback(() => {
    const rect = svg.current?.getBoundingClientRect();
    if (!rect || !rect.width || !rect.height) return 1;
    return Math.max(box.w / zoom / rect.width, box.h / zoom / rect.height);
  }, [box.w, box.h, zoom]);

  const onPointerDown = (e: Pointer<SVGSVGElement>) => {
    if (e.button !== 0) return;
    // Cleared here rather than after use: a gesture that ends outside the picture never produces a click,
    // and a flag left raised would swallow the next real one
    dragged.current = false;
    drag.current = { x: e.clientX, y: e.clientY, px: pan.x, py: pan.y, moved: false };
    e.currentTarget.setPointerCapture(e.pointerId);
  };
  const onPointerMove = (e: Pointer<SVGSVGElement>) => {
    const start = drag.current;
    if (!start) return;
    const dx = e.clientX - start.x;
    const dy = e.clientY - start.y;
    if (!start.moved && Math.abs(dx) + Math.abs(dy) < 4) return;
    start.moved = true;
    dragged.current = true;
    const s = scale();
    // Beyond a graph's width in either direction there is nothing left to look at
    const clamp = (v: number, span: number) => Math.max(-span, Math.min(span, v));
    setPan({ x: clamp(start.px - dx * s, box.w), y: clamp(start.py - dy * s, box.h) });
  };
  const onPointerUp = (e: Pointer<SVGSVGElement>) => {
    if (drag.current && e.currentTarget.hasPointerCapture(e.pointerId)) {
      e.currentTarget.releasePointerCapture(e.pointerId);
    }
    drag.current = null;
  };

  const view = `${box.x + (box.w * (1 - 1 / zoom)) / 2 + pan.x} ${box.y + (box.h * (1 - 1 / zoom)) / 2 + pan.y} ${box.w / zoom} ${box.h / zoom}`;

  const toggle = (kind: string) => {
    const next = new Set(hidden);
    if (next.has(kind)) next.delete(kind);
    else next.add(kind);
    onHidden(next);
  };

  const chosen = useMemo(() => shown.edges.find((e) => edgeKey(e) === edge) ?? null, [shown.edges, edge]);

  if (!nodes.length) return null;

  return (
    <Stack gap="xs" style={fullscreen ? { height: '100%' } : undefined}>
      <Group justify="space-between" wrap="wrap" gap="xs">
        <Group gap={6} wrap="wrap">
          {/* One switch for the whole legend: on a rich catalogue the useful move is usually to put
              everything away and bring back the one or two kinds you are actually following */}
          <Tooltip label={allHidden ? 'Bring every kind back' : 'Hide every kind at once, then bring back the ones you want'}>
            <Button
              size="compact-xs"
              variant={allHidden ? 'filled' : 'default'}
              leftSection={allHidden ? <IconEye size={13} /> : <IconEyeOff size={13} />}
              onClick={() => onHidden(allHidden ? new Set() : new Set(kinds.map(([kind]) => kind)))}
            >
              {allHidden ? 'All' : 'None'}
            </Button>
          </Tooltip>
          {kinds.map(([kind, count]) => {
            const off = hidden.has(kind);
            return (
              <Tooltip key={kind} label={off ? 'Show this kind again' : 'Hide this kind of element'}>
                <Badge
                  size="sm"
                  variant={off ? 'outline' : 'light'}
                  color={off ? 'gray' : undefined}
                  style={{ cursor: 'pointer', opacity: off ? 0.55 : 1, textTransform: 'none' }}
                  onClick={() => toggle(kind)}
                  leftSection={
                    <Box
                      w={8}
                      h={8}
                      style={{
                        borderRadius: kind === 'source' ? 2 : '50%',
                        transform: kind === 'query' ? 'rotate(45deg)' : undefined,
                        background: off ? 'transparent' : (COLORS[kind] ?? 'var(--mantine-color-dark-4)'),
                        border: off ? '1px solid var(--mantine-color-dimmed)' : undefined,
                      }}
                    />
                  }
                >
                  {kindLabel(kind)} ({count})
                </Badge>
              </Tooltip>
            );
          })}
          {!!removed.size && (
            <Tooltip label="Bring back the relations you set aside">
              <Badge
                size="sm"
                variant="outline"
                color="orange"
                style={{ cursor: 'pointer', textTransform: 'none' }}
                leftSection={<IconUnlink size={11} />}
                onClick={() => onRemoved(new Set())}
              >
                {removed.size} relation(s) set aside
              </Badge>
            </Tooltip>
          )}
        </Group>
        <Group gap="xs" wrap="nowrap">
          <Tooltip
            label={
              live
                ? 'How far apart the nodes are held'
                : 'How far apart the nodes are held — applied when you let go, because the graph is large'
            }
          >
            <Group gap={6} wrap="nowrap">
              <IconArrowsMaximize size={16} style={{ color: 'var(--mantine-color-dimmed)' }} />
              <Slider
                w={120}
                min={0.5}
                max={3}
                step={0.1}
                value={dragging}
                label={(v) => `${v.toFixed(1)}×`}
                onChange={(v) => {
                  setDragging(v);
                  if (live) onSpacing(v);
                }}
                onChangeEnd={onSpacing}
                aria-label="Spacing between the nodes"
              />
            </Group>
          </Tooltip>
          <Tooltip label="Zoom out">
            <ActionIcon variant="default" onClick={() => setZoom((z) => Math.max(1, z / 1.3))} aria-label="Zoom out">
              <IconZoomOut size={16} />
            </ActionIcon>
          </Tooltip>
          <Tooltip label="Fit the whole graph again">
            <ActionIcon variant="default" onClick={fit} aria-label="Fit">
              <IconFocusCentered size={16} />
            </ActionIcon>
          </Tooltip>
          <Tooltip label="Zoom in">
            <ActionIcon variant="default" onClick={() => setZoom((z) => Math.min(6, z * 1.3))} aria-label="Zoom in">
              <IconZoomIn size={16} />
            </ActionIcon>
          </Tooltip>
          {onFullscreen && (
            <Tooltip label={fullscreen ? 'Leave the enlarged view' : 'Enlarge the graph'}>
              <ActionIcon
                variant={fullscreen ? 'filled' : 'default'}
                onClick={() => onFullscreen(!fullscreen)}
                aria-label={fullscreen ? 'Leave full screen' : 'Full screen'}
              >
                {fullscreen ? <IconMinimize size={16} /> : <IconMaximize size={16} />}
              </ActionIcon>
            </Tooltip>
          )}
        </Group>
      </Group>

      {/* A relation under consideration: what it asserts, and the offer to take it out of the picture */}
      {chosen && (
        <Group
          gap="xs"
          wrap="nowrap"
          p={6}
          style={{
            border: '1px solid var(--mantine-color-default-border)',
            borderRadius: 'var(--mantine-radius-sm)',
            background: 'var(--mantine-color-default-hover)',
          }}
        >
          <IconUnlink size={16} style={{ color: 'var(--mantine-color-dimmed)', flexShrink: 0 }} />
          <Text size="xs" style={{ minWidth: 0 }} truncate>
            <b>{byId.get(chosen.source)?.label ?? chosen.source}</b>
            {chosen.kind === 'same_row' ? ' shares a row with ' : ' is in '}
            <b>{byId.get(chosen.target)?.label ?? chosen.target}</b> — {chosen.label} in {chosen.referential_id}
            {chosen.rows ? `, ${chosen.rows} row(s)` : ''}
          </Text>
          <Button
            size="compact-xs"
            color="orange"
            variant="light"
            leftSection={<IconUnlink size={13} />}
            onClick={() => {
              onRemoved(new Set([...removed, edgeKey(chosen)]));
              setEdge(null);
            }}
          >
            Set it aside
          </Button>
          <Button size="compact-xs" variant="subtle" onClick={() => setEdge(null)}>
            Keep it
          </Button>
        </Group>
      )}

      <Box
        style={{
          border: '1px solid var(--mantine-color-default-border)',
          borderRadius: 'var(--mantine-radius-md)',
          background: 'var(--mantine-color-body)',
          overflow: 'hidden',
          flex: fullscreen ? 1 : undefined,
          minHeight: 0,
        }}
      >
        <svg
          ref={svg}
          viewBox={view}
          height={fullscreen ? '100%' : height}
          width="100%"
          role="img"
          aria-label={`Relations of the investigation: ${shown.nodes.length} nodes, ${shown.edges.length} relations`}
          style={{ cursor: 'grab', touchAction: 'none' }}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
          onClick={() => {
            // The end of a drag reaches here as a click: it is a move of the view, not a change of selection
            if (dragged.current) {
              dragged.current = false;
              return;
            }
            onSelect?.(null);
            setEdge(null);
          }}
        >
          <defs>
            <marker id="rg-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto">
              <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--mantine-color-dimmed)" />
            </marker>
          </defs>

          {shown.edges.map((edgeOf, i) => {
            const a = byId.get(edgeOf.source);
            const b = byId.get(edgeOf.target);
            if (!a || !b) return null;
            const dim = near !== null && !(near.has(edgeOf.source) && near.has(edgeOf.target));
            const key = edgeKey(edgeOf);
            const picked = key === edge;
            return (
              <g key={`${key}-${i}`}>
                {/* A line two pixels wide is not a target: an invisible fat one carries the pointer */}
                <line
                  x1={a.x}
                  y1={a.y}
                  x2={b.x}
                  y2={b.y}
                  stroke="transparent"
                  strokeWidth={10}
                  style={{ cursor: 'pointer' }}
                  onClick={(e) => {
                    e.stopPropagation();
                    if (dragged.current) {
                      dragged.current = false;
                      return;
                    }
                    setEdge((current) => (current === key ? null : key));
                  }}
                >
                  <title>
                    {edgeOf.kind === 'same_row'
                      ? `${a.label} and ${b.label} share a row of ${edgeOf.referential_id} (${edgeOf.label}) — click to set this relation aside`
                      : `${a.label} is in ${edgeOf.referential_id} (${edgeOf.label}) — click to set this relation aside`}
                  </title>
                </line>
                <line
                  x1={a.x}
                  y1={a.y}
                  x2={b.x}
                  y2={b.y}
                  stroke={
                    picked
                      ? 'var(--mantine-color-orange-6)'
                      : edgeOf.kind === 'same_row'
                        ? 'var(--mantine-color-blue-5)'
                        : 'var(--mantine-color-default-border)'
                  }
                  strokeWidth={picked ? 3 : edgeOf.kind === 'same_row' ? 1.6 : 1.1}
                  strokeDasharray={edgeOf.kind === 'found_in' ? '4 3' : undefined}
                  opacity={dim && !picked ? 0.12 : 0.75}
                  markerEnd={edgeOf.kind === 'same_row' ? 'url(#rg-arrow)' : undefined}
                  style={{ pointerEvents: 'none' }}
                />
              </g>
            );
          })}

          {placed.map((node) => {
            const dim = near !== null && !near.has(node.id);
            const r = radius(node);
            const isSelected = selected === node.id;
            const ring = seedRing(node, several);
            const meeting = several && (node.seeds ?? []).length > 1;
            return (
              <g
                key={node.id}
                opacity={dim ? 0.25 : 1}
                style={{ cursor: 'pointer' }}
                onMouseEnter={() => setHovered(node.id)}
                onMouseLeave={() => setHovered(null)}
                onClick={(e) => {
                  e.stopPropagation();
                  if (dragged.current) {
                    dragged.current = false;
                    return;
                  }
                  setEdge(null);
                  onSelect?.(node);
                }}
              >
                <title>
                  {node.kind === 'source'
                    ? `${node.label} — ${node.rows} row(s) matched`
                    : node.kind === 'query'
                      ? `Text searched: ${node.value} — ${node.rows} row(s) mention it`
                      : `${node.entity}: ${node.value}${node.rows ? ` — in ${node.referentials.length} referential(s)` : ''}`}
                </title>
                {node.kind === 'query' ? (
                  <polygon
                    points={`${node.x},${node.y - r - 3} ${node.x + r + 3},${node.y} ${node.x},${node.y + r + 3} ${node.x - r - 3},${node.y}`}
                    fill={color(node)}
                    stroke="var(--mantine-color-bright)"
                    strokeWidth={2}
                  />
                ) : node.kind === 'source' ? (
                  <rect
                    x={node.x - r}
                    y={node.y - r}
                    width={r * 2}
                    height={r * 2}
                    rx={3}
                    fill={color(node)}
                    stroke={isSelected ? 'var(--mantine-color-bright)' : 'var(--mantine-color-body)'}
                    strokeWidth={isSelected ? 2.5 : 1.5}
                  />
                ) : (
                  <circle
                    cx={node.x}
                    cy={node.y}
                    r={r}
                    fill={color(node)}
                    stroke={isSelected || node.seed ? 'var(--mantine-color-bright)' : 'var(--mantine-color-body)'}
                    strokeWidth={isSelected ? 2.5 : node.seed ? 2 : 1.5}
                  />
                )}
                {/* Which seed reached this node, and a double ring where several did */}
                {ring && (
                  <circle
                    cx={node.x}
                    cy={node.y}
                    r={r + (meeting ? 5 : 3)}
                    fill="none"
                    stroke={ring}
                    strokeWidth={meeting ? 2.5 : 1.5}
                    opacity={dim ? 0.3 : 0.9}
                  />
                )}
                {/* Labels only where they can be read: the seed, the referentials, and whatever is in focus */}
                {(node.seed || node.kind === 'source' || !dim) && (
                  <text
                    x={node.x}
                    y={node.y + r + 11}
                    textAnchor="middle"
                    fontSize={node.seed ? 12 : 10}
                    fontWeight={node.seed ? 700 : 400}
                    fill="var(--mantine-color-text)"
                    style={{ pointerEvents: 'none', userSelect: 'none' }}
                  >
                    {shorten(node.label, node.seed ? 34 : 22)}
                  </text>
                )}
              </g>
            );
          })}
        </svg>
      </Box>
      <Text size="xs" c="dimmed">
        A circle is a value, a square a referential, a diamond the text you searched for. A dashed line puts
        the value <b>in</b> that referential; a solid arrow means the two values <b>share a row</b> of it —
        that row is the evidence. Hover a node to isolate its relations, click it to see them.
        {several && (
          <>
            {' '}
            With several values, the ring around a node is the seed that reached it; a{' '}
            <b>yellow double ring</b> marks a value several of them reach — that is what ties them together.
          </>
        )}{' '}
        Use the spacing control if the picture is too tight, <b>drag the picture</b> to move around it once
        zoomed in, click a kind in the legend to hide it, and click a line to set that relation aside.
      </Text>
      {(!!hidden.size || !!removed.size) && (
        <Group gap={6} align="center" wrap="wrap">
          <IconHandStop size={14} style={{ color: 'var(--mantine-color-dimmed)' }} />
          <Text size="xs" c="dimmed">
            Showing {shown.nodes.length} of {nodes.length} nodes and {shown.edges.length} of {edges.length}{' '}
            relations.
            {hidden.size ? ` ${[...hidden].map(kindLabel).join(', ')} hidden — seeds are always shown.` : ''}
            {removed.size
              ? ` ${removed.size} relation(s) set aside: taken out of the picture, not out of the evidence.`
              : ''}
          </Text>
        </Group>
      )}
    </Stack>
  );
}
