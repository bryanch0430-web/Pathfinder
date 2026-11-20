export type NodeType =
  | 'COUNTRY'
  | 'CITY'
  | 'NEIGHBORHOOD'
  | 'ATTRACTION'
  | 'RESTAURANT'
  | 'ACTIVITY'
  | 'HOTEL'
  | 'TRANSPORT';

export interface GraphNode {
  id: string;
  type: NodeType;
  name: string;
  description?: string;
  position?: [number, number, number];
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  relationship: string;
  cost?: number;
  duration?: number;
}

export interface Trip {
  id: string;
  destination: string;
  days: number;
  budget: number;
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface TripFormData {
  destination: string;
  days: number;
  budget: number;
  interests: string[];
}
