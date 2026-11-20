import { useEffect, useMemo, useRef, useState } from 'react';
import { Canvas, useFrame, useThree } from '@react-three/fiber';
import { Html, Line, OrbitControls } from '@react-three/drei';
import { QuadraticBezierCurve3, Vector3, type Group } from 'three';
import gsap from 'gsap';
import type { Coordinates, ItineraryGraph, NearbyPlace } from './itineraryGraph';
import type { LegExpansion, LegFacts } from './spatialModel';

export type GraphLayout = 'geo' | 'helix' | 'line';
interface Props {
  /** The guide's portrait: it walks the selected leg and stands over the open stop. */
  guideImage?: string;
  graph: ItineraryGraph;
  layout: GraphLayout;
  night: boolean;
  coordinates: Record<string, Coordinates>;
  facts: Record<string, LegFacts>;
  nearby: NearbyPlace[];
  expandedNode: string | null;
  expandedEdge: string | null;
  expansion: LegExpansion | null;
  selected: string | null;
  onSelect: (kind: 'node' | 'edge' | 'nearby' | 'sub' | 'subleg', id: string) => void;
}
type Point = [number, number, number];

const reduced = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;

function layoutPoints(graph: ItineraryGraph, layout: GraphLayout, coordinates: Record<string, Coordinates>): Record<string, Point> {
  const n = graph.nodes.length;
  if (layout === 'helix') return Object.fromEntries(graph.nodes.map((node, i) => [node.id,
    [Math.cos(i * 0.95) * 6, i * 1.5 - (n - 1) * 0.75, Math.sin(i * 0.95) * 6]]));
  const located = graph.nodes.filter((node) => coordinates[node.id]);
  if (layout === 'line' || located.length < 2) return Object.fromEntries(graph.nodes.map((node, i) => [node.id,
    [i * 6.5 - (n - 1) * 3.25, 0, 0]]));
  // ponytail: flat equirectangular projection; fine at trip scale, wrong across the antimeridian.
  const stops = located.filter((node) => node.kind === 'stop');
  const frame = stops.length > 1 ? stops : located;
  const lat0 = frame.reduce((sum, node) => sum + coordinates[node.id].lat, 0) / frame.length;
  const lon0 = frame.reduce((sum, node) => sum + coordinates[node.id].lon, 0) / frame.length;
  const raw = (c: Coordinates) => [(c.lon - lon0) * Math.cos(lat0 * Math.PI / 180), -(c.lat - lat0)] as const;
  const span = Math.max(...frame.map((node) => Math.hypot(...raw(coordinates[node.id]))), 1e-3);
  const scale = 9 / span;
  let last: Point = [-10, 0, 0];
  return Object.fromEntries(graph.nodes.map((node) => {
    const c = coordinates[node.id];
    if (!c) { last = [last[0] + 3, 0, last[2]]; return [node.id, last]; }
    let [x, z] = raw(c).map((value) => value * scale);
    const r = Math.hypot(x, z);
    if (r > 14) { x = x / r * 14; z = z / r * 14; } // far origins pulled to the rim
    last = [x, 0, z];
    return [node.id, last];
  }));
}

function curveFor(a: Point, b: Point, layout: GraphLayout) {
  const va = new Vector3(...a); const vb = new Vector3(...b);
  const mid = va.clone().lerp(vb, 0.5);
  mid.y += layout === 'helix' ? 0.6 : 1.6 + va.distanceTo(vb) * 0.3;
  return new QuadraticBezierCurve3(va, mid, vb);
}

function CameraFocus({ focus, reach, home, controlsRef }: { focus: Point | null; reach: number; home: Point; controlsRef: React.RefObject<any> }) {
  const { camera } = useThree();
  useEffect(() => {
    const controls = controlsRef.current;
    if (!controls) return;
    const target = focus ? new Vector3(...focus) : new Vector3();
    const dir = camera.position.clone().sub(controls.target).normalize();
    const to = focus ? target.clone().add(dir.multiplyScalar(reach)) : new Vector3(...home);
    if (reduced()) { controls.target.copy(target); camera.position.copy(to); controls.update(); return; }
    const a = gsap.to(controls.target, { x: target.x, y: target.y, z: target.z, duration: 0.7, ease: 'power3.inOut', onUpdate: () => controls.update() });
    const b = gsap.to(camera.position, { x: to.x, y: to.y, z: to.z, duration: 0.7, ease: 'power3.inOut', onUpdate: () => controls.update() });
    return () => { a.kill(); b.kill(); };
  }, [camera, controlsRef, focus, reach, home]);
  return null;
}

// A bead that travels the selected leg — shows direction of travel.
function Traveler({ curve, color, image }: { curve: QuadraticBezierCurve3; color: string; image?: string }) {
  const ref = useRef<Group>(null);
  useFrame(({ clock }) => { if (ref.current) ref.current.position.copy(curve.getPoint((clock.elapsedTime * 0.28) % 1)); });
  if (reduced()) return null;
  return <group ref={ref}>
    {image
      ? <Html center zIndexRange={[25, 0]} style={{ pointerEvents: 'none' }}><img src={image} alt="" className="tv-g3__guide is-walking" draggable={false} /></Html>
      : <mesh><sphereGeometry args={[0.13, 16, 12]} /><meshBasicMaterial color={color} /></mesh>}
  </group>;
}

export function TripGraph3D({ guideImage, graph, layout, night, coordinates, facts, nearby, expandedNode, expandedEdge, expansion, selected, onSelect }: Props) {
  const [hover, setHover] = useState<string | null>(null);
  const controlsRef = useRef<any>(null);
  const points = useMemo(() => layoutPoints(graph, layout, coordinates), [graph, layout, coordinates]);
  const curves = useMemo(() => Object.fromEntries(graph.edges.flatMap((edge) =>
    points[edge.source] && points[edge.target] ? [[edge.id, curveFor(points[edge.source], points[edge.target], layout)]] : [])), [graph, points, layout]);
  const ink = night ? '#f4f3f1' : '#1c1c1a';
  const wire = night ? '#5b5954' : '#a8a59e';
  const accent = night ? '#e0a768' : '#a96d38';
  const count = graph.nodes.length;
  const home = useMemo<Point>(() => layout === 'helix' ? [0, 4, Math.max(20, count * 2.6)]
    : layout === 'geo' ? [0, 17, 17] : [0, 7, Math.max(20, count * 5.5)], [layout, count]);
  const parent = expandedNode ? points[expandedNode] : null;
  const edgeCurve = expandedEdge ? curves[expandedEdge] : null;
  const focus = useMemo<Point | null>(() => parent || (edgeCurve ? edgeCurve.getPoint(0.5).toArray() as Point : null), [parent, edgeCurve]);
  const chips = graph.edges.length <= 10;
  const satellites = parent ? nearby.slice(0, 8).map((place, i, all) => {
    const angle = (i / Math.max(all.length, 3)) * Math.PI * 2;
    const r = 2.3 + (i % 2) * 0.5;
    return { place, point: [parent[0] + Math.cos(angle) * r, parent[1] + 0.9 + (i % 2) * 0.5, parent[2] + Math.sin(angle) * r] as Point };
  }) : [];
  const pick = (kind: Parameters<Props['onSelect']>[0], id: string) => (event: { stopPropagation: () => void }) => { event.stopPropagation(); onSelect(kind, id); };
  const hoverOn = (id: string) => (event: { stopPropagation: () => void }) => { event.stopPropagation(); setHover(id); document.body.style.cursor = 'pointer'; };
  const hoverOff = () => { setHover(null); document.body.style.cursor = ''; };
  useEffect(() => () => { document.body.style.cursor = ''; }, []);

  return <div className={`tv-g3 ${night ? 'is-night' : ''}`} aria-label="Interactive three-dimensional itinerary graph">
    <Canvas camera={{ position: home, fov: 42 }} dpr={[1, 1.75]} onPointerMissed={() => setHover(null)}>
      {night && <fog attach="fog" args={['#101010', 26, 70]} />}
      <ambientLight intensity={night ? 0.9 : 1.6} />
      <directionalLight position={[6, 12, 8]} intensity={night ? 1.6 : 1.8} />
      {layout === 'geo'
        ? <polarGridHelper args={[16, 12, 6, 72, night ? '#2e2d2a' : '#d8d4cc', night ? '#1f1e1c' : '#ebe8e2']} position={[0, -0.02, 0]} />
        : <gridHelper args={[60, 30, night ? '#2e2d2a' : '#d8d4cc', night ? '#1c1b19' : '#eeebe5']}
          position={[0, layout === 'helix' ? -(graph.nodes.length * 0.75) - 1.5 : -1.6, 0]} />}

      {graph.edges.map((edge) => {
        const curve = curves[edge.id];
        if (!curve) return null;
        const active = selected === edge.id || expandedEdge === edge.id || hover === edge.id;
        const f = facts[edge.id];
        const dashed = f?.kind === 'air' || f?.kind === 'water';
        const mid = curve.getPoint(0.5).toArray() as Point;
        return <group key={edge.id}>
          <Line points={curve.getPoints(48)} color={active ? accent : wire} lineWidth={active ? 3 : 1.6}
            dashed={dashed} dashSize={0.3} gapSize={0.18} />
          {/* fat invisible tube for easy picking */}
          <mesh onClick={pick('edge', edge.id)} onPointerOver={hoverOn(edge.id)} onPointerOut={hoverOff}>
            <tubeGeometry args={[curve, 24, 0.3, 6, false]} />
            <meshBasicMaterial transparent opacity={0} depthWrite={false} />
          </mesh>
          {expandedEdge === edge.id && <Traveler curve={curve} color={accent} image={guideImage} />}
          {(chips || active) && f && expandedEdge !== edge.id && <Html position={mid} center zIndexRange={[20, 0]}>
            <button type="button" className={`tv-g3__chip ${active ? 'is-active' : ''}`} onClick={() => onSelect('edge', edge.id)}
              onPointerEnter={() => setHover(edge.id)} onPointerLeave={() => setHover(null)}>
              <b>{f.duration.value}</b><span>{f.distance.value}</span>{f.cost.value !== 'Not quoted' && <span>{f.cost.value}</span>}
              {f.fit.some((note) => note.tone === 'warn') && <i aria-label="Preference warning">!</i>}
            </button>
          </Html>}
        </group>;
      })}

      {edgeCurve && expansion && <group>
        {expansion.stops.map((stop, i) => {
          const at = edgeCurve.getPoint(i / (expansion.stops.length - 1)).toArray() as Point;
          const on = selected === stop.id || hover === stop.id;
          return stop.role === 'waypoint' && <group key={stop.id} position={at}>
            <mesh onClick={pick('sub', stop.id)} onPointerOver={hoverOn(stop.id)} onPointerOut={hoverOff}>
              <octahedronGeometry args={[on ? 0.26 : 0.2]} />
              <meshStandardMaterial color={on ? accent : ink} roughness={0.4} />
            </mesh>
            <Html position={[0, -0.55, 0]} center><span className="tv-g3__tag">{stop.at}</span></Html>
          </group>;
        })}
        {expansion.legs.map((leg, i) => {
          const at = edgeCurve.getPoint((i + 0.5) / expansion.legs.length).toArray() as Point;
          return <Html key={leg.id} position={[at[0], at[1] + 0.55, at[2]]} center>
            <button type="button" className={`tv-g3__chip is-sub ${selected === leg.id ? 'is-active' : ''}`} onClick={() => onSelect('subleg', leg.id)}>
              <b>{leg.duration}</b><span>{leg.distance}</span>
            </button>
          </Html>;
        })}
      </group>}

      {graph.nodes.map((node, index) => {
        const p = points[node.id];
        const on = selected === node.id || expandedNode === node.id;
        const hot = on || hover === node.id;
        return <group key={node.id} position={p}>
          {layout === 'line' && <Line points={[[0, -1.2, 0], [0, -1.6, 0]]} color={wire} lineWidth={1} transparent opacity={0.6} />}
          {on && guideImage && <Html position={[0, 1.15, 0]} center zIndexRange={[28, 0]} style={{ pointerEvents: 'none' }}><img src={guideImage} alt="" className="tv-g3__guide is-bounce" draggable={false} /></Html>}
          {on && <mesh rotation={[-Math.PI / 2, 0, 0]}><ringGeometry args={[0.62, 0.68, 48]} /><meshBasicMaterial color={accent} /></mesh>}
          <mesh onClick={pick('node', node.id)} onPointerOver={hoverOn(node.id)} onPointerOut={hoverOff}>
            <sphereGeometry args={[hot ? 0.44 : 0.36, 32, 20]} />
            <meshStandardMaterial color={on ? accent : node.kind === 'origin' ? (night ? '#8fa39c' : '#6e817b') : ink} roughness={0.35} metalness={0.1} />
          </mesh>
          <Html position={[0, -0.85, 0]} center zIndexRange={[30, 0]}>
            <button type="button" className={`tv-g3__label ${on ? 'is-active' : ''}`} onClick={() => onSelect('node', node.id)}
              onPointerEnter={() => setHover(node.id)} onPointerLeave={() => setHover(null)}>
              <em>{String(index + 1).padStart(2, '0')}</em>{node.name.length > 26 ? `${node.name.slice(0, 25)}…` : node.name}
              {node.day_start && <small>D{node.day_start}{node.day_end && node.day_end !== node.day_start ? `–${node.day_end}` : ''}</small>}
            </button>
          </Html>
        </group>;
      })}

      {satellites.map(({ place, point }) => {
        const on = selected === place.id || hover === place.id;
        return <group key={place.id}>
          <Line points={[parent!, point]} color={accent} lineWidth={1} dashed dashSize={0.1} gapSize={0.08} transparent opacity={0.7} />
          <group position={point}>
            <mesh onClick={pick('nearby', place.id)} onPointerOver={hoverOn(place.id)} onPointerOut={hoverOff}>
              <sphereGeometry args={[on ? 0.22 : 0.16, 18, 12]} />
              <meshStandardMaterial color={on ? accent : night ? '#c9a57d' : '#b08b62'} roughness={0.5} />
            </mesh>
            {on && <Html position={[0, 0.45, 0]} center><span className="tv-g3__tag is-strong">{place.name}{place.distance_km !== undefined ? ` · ${place.distance_km} km` : ''}</span></Html>}
          </group>
        </group>;
      })}

      <OrbitControls ref={controlsRef} makeDefault enableDamping dampingFactor={0.08} minDistance={4} maxDistance={80} maxPolarAngle={Math.PI * 0.49} />
      <CameraFocus focus={focus} reach={edgeCurve && !parent ? Math.max(9, edgeCurve.getLength() * 1.25) : 11} home={home} controlsRef={controlsRef} />
    </Canvas>
    <div className="tv-g3__hint">Drag to orbit · Scroll to zoom · Click a stop or leg to expand it</div>
  </div>;
}
