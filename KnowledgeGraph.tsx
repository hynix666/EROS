import React, { useEffect, useRef, useState } from 'react';
import { apiClient } from '../api/client';
import * as d3 from 'd3';
import { Node, Link, GraphData } from '../types';

import Markdown from 'react-markdown';
import { Network, Settings, ChevronUp, ChevronDown, ChevronRight, Mic, MicOff } from 'lucide-react';
import { useLocalStorageState } from '../hooks/useLocalStorageState';



const categorizeNode = (node: Node): number => {
  if (node.type) {
    const t = node.type.toLowerCase();
    if (t === 'project') return 1;
    if (t === 'research') return 2;
    if (t === 'code') return 3;
    if (t === 'data') return 4;
    if (t === 'system') return 5;
    if (t === 'component') return 6;
    if (t === 'action') return 7;
    let hash = 0;
    for (let i = 0; i < t.length; i++) hash = t.charCodeAt(i) + ((hash << 5) - hash);
    return (Math.abs(hash) % 7) + 1;
  }
  if (node.group !== undefined) return node.group;
  const idLower = node.id.toLowerCase();
  if (idLower.includes('core') || idLower.includes('project')) return 1;
  if (idLower.includes('research') || idLower.includes('agent') || idLower.includes('llm') || idLower.includes('search')) return 2;
  if (idLower.includes('code') || idLower.includes('gen') || idLower.includes('api') || idLower.includes('hardware')) return 3;
  if (idLower.includes('system')) return 5;
  if (idLower.includes('component')) return 6;
  if (idLower.includes('action')) return 7;
  return 4;
};

const getNodeColor = (node: Node): string => {
  if (node.type) {
    const t = node.type.toLowerCase();
    if (t === 'project') return 'var(--color-primary)';
    if (t === 'research') return 'var(--color-text-bright)';
    if (t === 'code') return 'var(--color-accent-1)';
    if (t === 'data') return 'var(--color-type-data)';
    if (t === 'system') return 'var(--color-type-system)';
    if (t === 'component') return 'var(--color-type-component)';
    if (t === 'action') return 'var(--color-type-action)';
    if (t === 'agent') return 'var(--color-type-agent)';
    if (t === 'logic') return 'var(--color-type-logic)';
    return `var(--color-type-${t}, var(--color-accent-2))`;
  }
  const group = categorizeNode(node);
  if (group === 1) return 'var(--color-primary)';
  if (group === 2) return 'var(--color-text-bright)';
  if (group === 3) return 'var(--color-accent-1)';
  if (group === 5) return 'var(--color-type-system)';
  if (group === 6) return 'var(--color-type-component)';
  if (group === 7) return 'var(--color-type-action)';
  return 'var(--color-accent-2)';
};

const initialData = {
  nodes: [
    { id: 'EROS Core', type: 'Project', description: 'Core Loop of Enterprise Research Operating System' },
    
    // UI
    { id: 'Web UI', type: 'Component', description: 'Research workspace, evidence browser, and run monitor' },
    { id: 'CLI', type: 'Component', description: 'Command-line interface' },
    
    // LIL
    { id: 'API Gateway (LIL)', type: 'System', description: 'Universal Interface Layer, Sync API + Async Event Bus' },
    
    // Orchestration
    { id: 'LangGraph', type: 'Logic', description: 'Research Workflow & Loop Engine' },
    { id: 'Heuristic Gate', type: 'Logic', description: 'Rules fast-path + local classifier for budget envelope' },
    { id: 'Budget Governor', type: 'Logic', description: 'Owned reservation ledger for API spend' },
    { id: 'Human Gate', type: 'Logic', description: '24h timeout approval state' },
    
    // Agents
    { id: 'Planner', type: 'Agent', description: 'Decomposes question into task list' },
    { id: 'Searcher', type: 'Agent', description: 'Fans out via Connector Framework' },
    { id: 'Ingestor', type: 'Agent', description: 'Fetch, parse, chunk, embed, classify, write' },
    { id: 'Retriever', type: 'Agent', description: 'Hybrid pgvector HNSW + Postgres FTS/BM25' },
    { id: 'Analyst', type: 'Agent', description: 'Drafts claims from retrieved evidence' },
    { id: 'Verifier', type: 'Agent', description: 'Citation validity, cross-check, contradiction detection' },
    { id: 'Arbiter', type: 'Agent', description: 'Adjudicates contested claims' },
    { id: 'Reporter', type: 'Agent', description: 'Assembles report from verified claims' },
    { id: 'QA-Eval', type: 'Agent', description: 'Samples sentences for groundedness using Judge' },
    
    // Trust Chain
    { id: 'Gate 1 (Evidence)', type: 'Logic', description: 'DB NOT NULL FK: a claim with no primary evidence cannot commit' },
    { id: 'Gate 2 (Publish)', type: 'Logic', description: 'DB constraint: publish refused if any claim lacks verified evidence' },
    { id: 'Gate 3 (Report Ledger)', type: 'Logic', description: 'Every sentence: verified claim | template | disclosed synthesis' },
    { id: 'Gate 4 (DGK)', type: 'Logic', description: 'Deterministic Groundedness Kernel: blocking, no model participates' },
    { id: 'Attested XFAM Check', type: 'Logic', description: 'DB trigger preventing fake cross-family verification labels' },
    
    // AI Inference
    { id: 'Model Router', type: 'System', description: 'Sensitivity, Lineage, Slot Availability rules' },
    { id: 'Slot Ledger', type: 'System', description: 'Sequential slots: Generation Slot & On-Demand Slot' },
    { id: 'Ollama', type: 'System', description: 'Local inference engine on ROCm' },
    { id: 'CPU Classifier', type: 'System', description: 'Unified CPU Classifier Service (Phi-4-mini)' },
    { id: 'Embedder & Reranker', type: 'System', description: 'AVX-512 VNNI CPU inference' },
    
    // Data
    { id: 'PostgreSQL 16', type: 'Data', description: 'The ONLY transactional store (runs, checkpoints, chunks, claims)' },
    { id: 'Object Store', type: 'Data', description: 'NVMe /data/artifacts' },
    { id: 'Event Bus', type: 'Data', description: 'Postgres LISTEN/NOTIFY + events table' },
    
    // External Connectors
    { id: 'Playwright Pool', type: 'Component', description: 'Sandboxed browser workers for JS-heavy pages' },
    { id: 'Connector Framework', type: 'Component', description: 'API-first connectors (Bing, OpenAlex, Crossref)' },
    
    // Security
    { id: 'Sensitivity Policy', type: 'Logic', description: 'Evaluated per claim, hard constraint in Router' },
    { id: 'WORM Audit', type: 'Data', description: 'Immutable audit history' }
  ] as Node[],
  links: [
    { source: 'Web UI', target: 'API Gateway (LIL)', value: 1, label: 'Connects to' },
    { source: 'CLI', target: 'API Gateway (LIL)', value: 1, label: 'Connects to' },
    
    { source: 'API Gateway (LIL)', target: 'Heuristic Gate', value: 1, label: 'Classifies via' },
    { source: 'Heuristic Gate', target: 'CPU Classifier', value: 1, label: 'Uses' },
    { source: 'API Gateway (LIL)', target: 'LangGraph', value: 2, label: 'Routes to' },
    { source: 'API Gateway (LIL)', target: 'Event Bus', value: 1, label: 'Publishes to' },
    
    { source: 'LangGraph', target: 'Budget Governor', value: 1, label: 'Reserves budget' },
    { source: 'LangGraph', target: 'Human Gate', value: 1, label: 'Checks approval' },
    { source: 'LangGraph', target: 'PostgreSQL 16', value: 3, label: 'Checkpoints state' },
    
    // Agent Orchestration
    { source: 'LangGraph', target: 'Planner', value: 1, label: 'Phase 1' },
    { source: 'LangGraph', target: 'Searcher', value: 1, label: 'Phase 2' },
    { source: 'LangGraph', target: 'Ingestor', value: 1, label: 'Phase 3' },
    { source: 'LangGraph', target: 'Retriever', value: 1, label: 'Phase 4' },
    { source: 'LangGraph', target: 'Analyst', value: 1, label: 'Phase 5' },
    { source: 'LangGraph', target: 'Verifier', value: 1, label: 'Phase 6' },
    { source: 'LangGraph', target: 'Arbiter', value: 1, label: 'Phase 7' },
    { source: 'LangGraph', target: 'Reporter', value: 1, label: 'Phase 8' },
    { source: 'LangGraph', target: 'QA-Eval', value: 1, label: 'Phase 9' },
    
    // Agent Actions
    { source: 'Searcher', target: 'Connector Framework', value: 1, label: 'Uses' },
    { source: 'Searcher', target: 'Playwright Pool', value: 1, label: 'Uses' },
    
    { source: 'Ingestor', target: 'Object Store', value: 2, label: 'Writes artifacts' },
    { source: 'Ingestor', target: 'PostgreSQL 16', value: 2, label: 'Writes chunks' },
    { source: 'Ingestor', target: 'Embedder & Reranker', value: 1, label: 'Embeds' },
    
    { source: 'Retriever', target: 'PostgreSQL 16', value: 2, label: 'Queries chunks' },
    { source: 'Retriever', target: 'Embedder & Reranker', value: 1, label: 'Reranks' },
    
    { source: 'Analyst', target: 'Gate 1 (Evidence)', value: 1, label: 'Must pass' },
    { source: 'Gate 1 (Evidence)', target: 'PostgreSQL 16', value: 1, label: 'Stages claims' },
    
    { source: 'Verifier', target: 'Attested XFAM Check', value: 1, label: 'Must pass' },
    { source: 'Verifier', target: 'Gate 4 (DGK)', value: 1, label: 'Must pass' },
    { source: 'Gate 4 (DGK)', target: 'PostgreSQL 16', value: 1, label: 'Writes verified claims' },
    
    { source: 'Reporter', target: 'Gate 3 (Report Ledger)', value: 1, label: 'Must pass' },
    { source: 'Gate 3 (Report Ledger)', target: 'Gate 2 (Publish)', value: 1, label: 'Must pass' },
    
    // Inference & Routing
    { source: 'Analyst', target: 'Model Router', value: 1, label: 'Infer (Drafter)' },
    { source: 'Verifier', target: 'Model Router', value: 1, label: 'Infer (Checker)' },
    { source: 'Arbiter', target: 'Model Router', value: 1, label: 'Infer (Arbiter)' },
    { source: 'QA-Eval', target: 'Model Router', value: 1, label: 'Infer (Judge)' },
    
    { source: 'Model Router', target: 'Sensitivity Policy', value: 2, label: 'Checks constraint' },
    { source: 'Model Router', target: 'Slot Ledger', value: 2, label: 'Checks availability' },
    { source: 'Slot Ledger', target: 'Ollama', value: 3, label: 'Manages load/evict' },
    
    { source: 'PostgreSQL 16', target: 'WORM Audit', value: 1, label: 'Secures logs' },
    { source: 'Event Bus', target: 'PostgreSQL 16', value: 1, label: 'Persists events' }
  ]
};

export default function KnowledgeGraph() {
  const svgRef = useRef<SVGSVGElement>(null);
  const miniSvgRef = useRef<SVGSVGElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [dimensions, setDimensions] = useState({ width: 0, height: 0 });

  useEffect(() => {
    const observeTarget = containerRef.current;
    if (!observeTarget) return;

    const observer = new ResizeObserver((entries) => {
      if (!entries || entries.length === 0) return;
      const { width, height } = entries[0].contentRect;
      setDimensions({ width, height });
    });

    observer.observe(observeTarget);
    return () => observer.unobserve(observeTarget);
  }, []);

  const [graphData, setGraphData] = useLocalStorageState('eros-graph-data', initialData);
  const [simulationKey, setSimulationKey] = useState(0);
  const [isLegendOpen, setIsLegendOpen] = useState(false);
  const [isClusterAll, setIsClusterAll] = useState(false);
  const [isHighlightNeighbors, setIsHighlightNeighbors] = useState(false);
  const [isSnapToGrid, setIsSnapToGrid] = useState(false);
  const [showLabels, setShowLabels] = useState(true);
  const [showTransparency, setShowTransparency] = useState(false);
  const [graphQuery, setGraphQuery] = useState('');
  const [isQuerying, setIsQuerying] = useState(false);
  const [isListening, setIsListening] = useState(false);
  const recognitionRef = useRef<any>(null);
  const [layoutPreset, setLayoutPreset] = useState<'Force-Directed' | 'Radial' | 'Concentric' | 'Hierarchical' | 'Tree' | 'Adaptive'>('Adaptive');
  const [contextMenu, setContextMenu] = useState<{ x: number, y: number, nodeId: string } | null>(null);
  const [hoverNode, setHoverNode] = useState<{ x: number, y: number, node: Node } | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedNodeIds, setSelectedNodeIds] = useState<string[]>([]);
  const [selectionHistory, setSelectionHistory] = useState<string[]>([]);
  const [highlightedPath, setHighlightedPath] = useState<Set<string> | null>(null);
  const [isEditingDescription, setIsEditingDescription] = useState(false);
  const [physicsRepulsion, setPhysicsRepulsion] = useState(250);
  const [physicsLinkDistance, setPhysicsLinkDistance] = useState(60);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [descriptionDraft, setDescriptionDraft] = useState('');
  const zoomRef = useRef<d3.ZoomBehavior<Element, unknown> | null>(null);
  const svgSelectionRef = useRef<d3.Selection<SVGSVGElement, unknown, null, undefined> | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const bulkImportRef = useRef<HTMLInputElement>(null);
  const compareInputRef = useRef<HTMLInputElement>(null);

  const graphDataRef = useRef(graphData);
  const nodeStateRef = useRef(new Map<string, {x: number, y: number, vx: number, vy: number}>());
  useEffect(() => {
    graphDataRef.current = graphData;
  }, [graphData]);

  useEffect(() => {
    setIsEditingDescription(false);
    if (selectedNodeId) {
      setSelectionHistory(prev => {
        if (prev[prev.length - 1] === selectedNodeId) return prev;
        return [...prev, selectedNodeId].slice(-6);
      });
    }
  }, [selectedNodeId]);

  useEffect(() => {
    const handleClearGraph = () => {
      setGraphData({ nodes: [], links: [] });
    };
    const handleClickOutside = () => {
      setContextMenu(null);
    };
    const handleAutomateResult = (e: any) => {
      const { nodes, links } = e.detail;
      if (!nodes || !links) return;
      
      setGraphData(prev => {
        const newNodes = [...prev.nodes];
        const newLinks = [...prev.links];
        
        nodes.forEach((n: any) => {
          if (!newNodes.find((ext: any) => ext.id === n.id)) {
            newNodes.push({ id: n.id, type: (n.type as any) || 'Data' });
          }
        });
        
        links.forEach((l: any) => {
          const sourceExists = newNodes.find((ext: any) => ext.id === l.source);
          const targetExists = newNodes.find((ext: any) => ext.id === l.target);
          if (sourceExists && targetExists) {
            // Check if link already exists
            const linkExists = newLinks.find((ext: any) => (typeof ext.source === 'object' ? ext.source.id : ext.source) === l.source && (typeof ext.target === 'object' ? ext.target.id : ext.target) === l.target);
            if (!linkExists) {
              newLinks.push({ source: l.source, target: l.target, value: l.value || 1, label: l.label, agent: 'Automator Swarm' } as any);
            }
          }
        });
        
        return { nodes: newNodes, links: newLinks };
      });
      setSimulationKey(prev => prev + 1);
    };
    const handleCompileReport = () => {
      const data = graphDataRef.current;
      
      let markdown = '# Knowledge Graph Research Report\n\n';
      markdown += '## Entities\n\n';
      
      const nodesByType = data.nodes.reduce((acc, node) => {
        const type = (node as any).type || 'Uncategorized';
        if (!acc[type]) acc[type] = [];
        acc[type].push(node.id);
        return acc;
      }, {} as Record<string, string[]>);
      
      for (const [type, nodes] of Object.entries(nodesByType)) {
        markdown += `### ${type}\n`;
        nodes.forEach(n => { markdown += `- ${n}\n`; });
        markdown += '\n';
      }
      
      markdown += '## Relationships\n\n';
      data.links.forEach(link => {
        const sourceId = typeof link.source === 'object' ? (link.source as Node).id : link.source;
        const targetId = typeof link.target === 'object' ? (link.target as Node).id : link.target;
        const label = link.label || 'Connected to';
        markdown += `- **${sourceId}** --[${label}]--> **${targetId}**\n`;
      });
      
      const blob = new Blob([markdown], { type: 'text/markdown' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'research_report.md';
      a.click();
      URL.revokeObjectURL(url);
    };

    window.addEventListener('clear-graph', handleClearGraph);
    window.addEventListener('compile-report', handleCompileReport);
    window.addEventListener('click', handleClickOutside);
    window.addEventListener('automate-graph-result', handleAutomateResult);
    return () => {
      window.removeEventListener('clear-graph', handleClearGraph);
      window.removeEventListener('compile-report', handleCompileReport);
      window.removeEventListener('click', handleClickOutside);
      window.removeEventListener('automate-graph-result', handleAutomateResult);
    };
  }, []);

  const handleForceSimulation = () => {
    setSimulationKey(prev => prev + 1);
  };

  const handleRename = (nodeId: string) => {
    const newName = prompt('Enter new name for node:', nodeId);
    if (!newName || newName === nodeId) return;
    
    setGraphData(prev => {
      const nodes = prev.nodes.map(n => n.id === nodeId ? { ...n, id: newName } : n);
      const links = prev.links.map(l => ({
        ...l,
        source: (typeof l.source === 'object' ? (l.source as Node).id : l.source) === nodeId ? newName : l.source,
        target: (typeof l.target === 'object' ? (l.target as Node).id : l.target) === nodeId ? newName : l.target,
      }));
      return { nodes, links };
    });
    setContextMenu(null);
  };
  
  const handleDelete = (nodeId: string) => {
    setGraphData(prev => {
      const nodes = prev.nodes.filter(n => n.id !== nodeId);
      const links = prev.links.filter(l => {
        const sourceId = typeof l.source === 'object' ? (l.source as Node).id : l.source;
        const targetId = typeof l.target === 'object' ? (l.target as Node).id : l.target;
        return sourceId !== nodeId && targetId !== nodeId;
      });
      return { nodes, links };
    });
    setContextMenu(null);
  };
  
  const handleSetPriority = (nodeId: string) => {
    const priorityStr = prompt('Enter priority (High, Medium, Low):', 'High');
    if (!priorityStr) return;
    setGraphData(prev => {
      const nodes = prev.nodes.map(n => n.id === nodeId ? { ...n, priority: priorityStr } : n);
      return { ...prev, nodes };
    });
    setContextMenu(null);
  };

  const handleToggleLock = (nodeId: string) => {
    setGraphData(prev => {
      const nodes = prev.nodes.map(n => n.id === nodeId ? { ...n, isLocked: !n.isLocked } : n);
      return { ...prev, nodes };
    });
    setSimulationKey(prev => prev + 1);
    setContextMenu(null);
  };

  const handleSmartLink = async (targetId: string) => {
    if (!selectedNodeId) {
      alert("Please select a source node first.");
      setContextMenu(null);
      return;
    }
    
    setContextMenu(null);
    const sourceNode = graphData.nodes.find(n => n.id === selectedNodeId);
    const targetNode = graphData.nodes.find(n => n.id === targetId);
    
    if (!sourceNode || !targetNode) return;
    
    try {
      const res = await fetch('/api/smart-link', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sourceNode, targetNode })
      });
      const data = await res.json();
      
      if (data.label && data.justification) {
        const confirmed = confirm(`Smart Link Suggestion:\n\nLabel: ${data.label}\nJustification: ${data.justification}\n\nAdd this connection?`);
        if (confirmed) {
          setGraphData(prev => {
            const newLinks = [...prev.links];
            const exists = newLinks.findIndex(l => {
              const s = typeof l.source === 'object' && l.source !== null ? (l.source as any).id : l.source;
              const t = typeof l.target === 'object' && l.target !== null ? (l.target as any).id : l.target;
              return s === sourceNode.id && t === targetNode.id;
            });
            
            if (exists >= 0) {
              newLinks[exists] = { ...newLinks[exists], label: data.label, rationale: data.justification, agent: 'EROS Architect' } as any;
            } else {
              newLinks.push({ source: sourceNode.id, target: targetNode.id, value: 1, label: data.label, rationale: data.justification, agent: 'EROS Architect' } as any);
            }
            return { ...prev, links: newLinks };
          });
          setSimulationKey(prev => prev + 1);
        }
      }
    } catch (err) {
      console.error(err);
      alert("Failed to create Smart Link.");
    }
  };

  const handleFindConnection = (targetId: string) => {
    if (!selectedNodeId) {
      alert("Please select a node first to find a connection.");
      setContextMenu(null);
      return;
    }
    if (selectedNodeId === targetId) {
      alert("Select a different node.");
      setContextMenu(null);
      return;
    }
    
    const adj = new Map<string, string[]>();
    graphData.links.forEach(l => {
      const s = typeof l.source === 'object' ? (l.source as Node).id : l.source;
      const t = typeof l.target === 'object' ? (l.target as Node).id : l.target;
      if (!adj.has(s)) adj.set(s, []);
      if (!adj.has(t)) adj.set(t, []);
      adj.get(s)!.push(t);
      adj.get(t)!.push(s);
    });
    
    const queue: string[] = [selectedNodeId];
    const visited = new Set<string>([selectedNodeId]);
    const parent = new Map<string, string>();
    
    let found = false;
    while (queue.length > 0) {
      const curr = queue.shift()!;
      if (curr === targetId) {
        found = true;
        break;
      }
      for (const neighbor of (adj.get(curr) || [])) {
        if (!visited.has(neighbor)) {
          visited.add(neighbor);
          parent.set(neighbor, curr);
          queue.push(neighbor);
        }
      }
    }
    
    if (found) {
      const path: string[] = [];
      let curr = targetId;
      while (curr !== selectedNodeId) {
        path.push(curr);
        curr = parent.get(curr)!;
      }
      path.push(selectedNodeId);
      path.reverse();
      
      const linkIds = new Set<string>();
      for (let i = 0; i < path.length - 1; i++) {
        linkIds.add(`${path[i]}-${path[i+1]}`);
        linkIds.add(`${path[i+1]}-${path[i]}`);
      }
      setHighlightedPath(linkIds);
    } else {
      alert("No connection found.");
      setHighlightedPath(null);
    }
    setContextMenu(null);
  };

  const handleAddConnection = async (sourceId: string) => {
    const targetId = prompt('Enter the ID of the node to connect to (will create if missing):');
    if (!targetId || targetId === sourceId) return;

    setContextMenu(null);

    const sourceNode = graphData.nodes.find(n => n.id === sourceId) || { id: sourceId, type: 'Data' as const };
    const targetNode = graphData.nodes.find(n => n.id === targetId) || { id: targetId, type: 'Data' as const };

    try {
      const response = await fetch('/api/infer-relationship', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sourceNode, targetNode })
      });
      const data = await response.json();
      const label = data.label || 'Connected to';

      setGraphData(prev => {
        const nodeExists = prev.nodes.some(n => n.id === targetId);
        const nodes = nodeExists ? prev.nodes : [...prev.nodes, { id: targetId, type: 'Data' as const }];
        const newLink = {
          source: sourceId,
          target: targetId,
          value: 1,
          label
        };
        return { nodes, links: [...prev.links, newLink] };
      });
      setSimulationKey(prev => prev + 1);
    } catch (e) {
      console.error(e);
      setGraphData(prev => {
        const nodeExists = prev.nodes.some(n => n.id === targetId);
        const nodes = nodeExists ? prev.nodes : [...prev.nodes, { id: targetId, type: 'Data' as const }];
        return { 
          nodes, 
          links: [...prev.links, { source: sourceId, target: targetId, value: 1, label: 'Connected to', agent: 'User' }]
        };
      });
      setSimulationKey(prev => prev + 1);
    }
  };

  const handleZoomToFit = () => {
    if (!zoomRef.current || !svgSelectionRef.current || !containerRef.current) return;
    
    // Find the bounding box of all nodes
    if (graphData.nodes.length === 0) return;
    
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    graphData.nodes.forEach((n: any) => {
      const state = nodeStateRef.current.get(n.id);
      const x = state ? state.x : 0;
      const y = state ? state.y : 0;
      const r = 30; // Approximation for bounds
      if (x - r < minX) minX = x - r;
      if (y - r < minY) minY = y - r;
      if (x + r > maxX) maxX = x + r;
      if (y + r > maxY) maxY = y + r;
    });
    
    const width = containerRef.current.clientWidth;
    const height = containerRef.current.clientHeight;
    
    const dx = maxX - minX;
    const dy = maxY - minY;
    
    if (dx === 0 || dy === 0) return;
    
    const x = (minX + maxX) / 2;
    const y = (minY + maxY) / 2;
    
    const scale = Math.max(0.1, Math.min(4, 0.85 / Math.max(dx / width, dy / height)));
    const translate = [width / 2 - scale * x, height / 2 - scale * y];
    
    const transform = d3.zoomIdentity.translate(translate[0], translate[1]).scale(scale);
    svgSelectionRef.current.transition().duration(750).call(zoomRef.current.transform as any, transform);
  };

  const handleZoomIn = () => {
    if (!zoomRef.current || !svgSelectionRef.current) return;
    svgSelectionRef.current.transition().duration(250).call(zoomRef.current.scaleBy as any, 1.2);
  };

  const handleZoomOut = () => {
    if (!zoomRef.current || !svgSelectionRef.current) return;
    svgSelectionRef.current.transition().duration(250).call(zoomRef.current.scaleBy as any, 0.8);
  };


  const handleBulkDelete = () => {
    setGraphData(prev => {
      const nodes = prev.nodes.filter(n => !selectedNodeIds.includes(n.id));
      const links = prev.links.filter(l => {
        const sourceId = typeof l.source === 'object' ? (l.source as Node).id : l.source;
        const targetId = typeof l.target === 'object' ? (l.target as Node).id : l.target;
        return !selectedNodeIds.includes(sourceId) && !selectedNodeIds.includes(targetId);
      });
      return { ...prev, nodes, links };
    });
    setSelectedNodeIds([]);
    setSelectedNodeId(null);
  };

  const handleBulkHighlight = () => {
    const paths = new Set<string>();
    graphData.links.forEach(l => {
        const sourceId = typeof l.source === 'object' ? (l.source as Node).id : l.source;
        const targetId = typeof l.target === 'object' ? (l.target as Node).id : l.target;
        if (selectedNodeIds.includes(sourceId) && selectedNodeIds.includes(targetId)) {
            paths.add(`${sourceId}-${targetId}`);
        }
    });
    setHighlightedPath(paths);
  };

  const executeQuery = async (queryText: string) => {
    if (!queryText.trim()) return;
    setIsQuerying(true);
    try {
      const res = await fetch('/api/query-graph', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: queryText, graphData })
      });
      const data = await res.json();
      if (data.nodeIds && Array.isArray(data.nodeIds)) {
        setSelectedNodeIds(data.nodeIds);
        if (data.nodeIds.length > 0) {
          setSelectedNodeId(data.nodeIds[0]);
        } else {
          setSelectedNodeId(null);
        }
      }
    } catch (err) {
      console.error(err);
      alert('Failed to execute graph query.');
    } finally {
      setIsQuerying(false);
    }
  };

  const handleQueryGraph = async (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      executeQuery(graphQuery);
    }
  };

  const toggleMic = () => {
    if (isListening) {
      recognitionRef.current?.stop();
      setIsListening(false);
      return;
    }

    if (!('webkitSpeechRecognition' in window) && !('SpeechRecognition' in window)) {
      alert('Speech recognition is not supported in this browser.');
      return;
    }

    const SpeechRecognition = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    const recognition = new SpeechRecognition();
    recognition.continuous = false;
    recognition.interimResults = true;
    recognitionRef.current = recognition;

    recognition.onstart = () => {
      setIsListening(true);
    };

    let finalTranscript = '';

    recognition.onresult = (event: any) => {
      const transcript = Array.from(event.results)
        .map((result: any) => result[0])
        .map((result: any) => result.transcript)
        .join('');
      setGraphQuery(transcript);
      finalTranscript = transcript;
    };

    recognition.onerror = (event: any) => {
      console.error('Speech recognition error:', event.error);
      setIsListening(false);
    };

    recognition.onend = () => {
      setIsListening(false);
      if (finalTranscript) {
        executeQuery(finalTranscript);
      }
    };

    recognition.start();
  };

  const handleExport = () => {
    const exportData = {
      nodes: graphData.nodes.map(n => ({ id: n.id, type: n.type, group: n.group })),
      links: graphData.links.map(l => ({
        source: typeof l.source === 'object' ? (l.source as Node).id : l.source,
        target: typeof l.target === 'object' ? (l.target as Node).id : l.target,
        value: l.value
      }))
    };
    const dataStr = "data:text/json;charset=utf-8," + encodeURIComponent(JSON.stringify(exportData, null, 2));
    const downloadAnchorNode = document.createElement('a');
    downloadAnchorNode.setAttribute("href", dataStr);
    downloadAnchorNode.setAttribute("download", "knowledge_graph.json");
    document.body.appendChild(downloadAnchorNode);
    downloadAnchorNode.click();
    downloadAnchorNode.remove();
  };

  const handleExportMermaid = () => {
    let mermaid = "graph TD;\n";
    
    const sanitizeId = (id: string) => id.replace(/[^a-zA-Z0-9]/g, '_');

    // Add nodes with formatting
    graphData.nodes.forEach(n => {
      const type = n.type || "Data";
      // Mermaid shapes (e.g. data is rectangle, agent is circle/stadium)
      let shapeStart = "[";
      let shapeEnd = "]";
      if (type === "Agent" || type === "Action") {
         shapeStart = "((";
         shapeEnd = "))";
      }
      mermaid += `    ${sanitizeId(n.id)}${shapeStart}"${n.id}"${shapeEnd};\n`;
    });

    // Add edges
    graphData.links.forEach(l => {
        const sourceId = typeof l.source === 'object' ? (l.source as Node).id : l.source;
        const targetId = typeof l.target === 'object' ? (l.target as Node).id : l.target;
        const label = (l as any).label ? `|"${(l as any).label}"|` : '';
        mermaid += `    ${sanitizeId(sourceId as string)} -->${label} ${sanitizeId(targetId as string)};\n`;
    });

    const blob = new Blob([mermaid], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = 'knowledge-graph.mmd';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  };

  const handleExportSVG = () => {
    if (!svgRef.current) return;
    
    const svgClone = svgRef.current.cloneNode(true) as SVGSVGElement;
    
    const styleElement = document.createElement('style');
    const computedStyle = getComputedStyle(document.documentElement);
    
    const cssVars = [
      '--color-bg-main',
      '--color-bg-surface',
      '--color-bg-surface-hover',
      '--color-border-main',
      '--color-border-subtle',
      '--color-text-main',
      '--color-text-secondary',
      '--color-text-muted',
      '--color-primary',
      '--color-accent-1',
      '--color-accent-2'
    ];
    
    let styleString = ':root {\n';
    cssVars.forEach(v => {
      styleString += `  ${v}: ${computedStyle.getPropertyValue(v)};\n`;
    });
    styleString += '}\n';
    
    styleString += `
      text {
        font-family: "JetBrains Mono", monospace;
      }
    `;
    
    styleElement.textContent = styleString;
    svgClone.insertBefore(styleElement, svgClone.firstChild);
    
    const serializer = new XMLSerializer();
    let svgString = serializer.serializeToString(svgClone);
    
    if (!svgString.match(/^<svg[^>]+xmlns="http\:\/\/www\.w3\.org\/2000\/svg"/)) {
      svgString = svgString.replace(/^<svg/, '<svg xmlns="http://www.w3.org/2000/svg"');
    }
    
    const blob = new Blob([svgString], { type: 'image/svg+xml;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = 'knowledge-graph.svg';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  };

  const handleImport = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = (e) => {
      try {
        const data = JSON.parse(e.target?.result as string);
        if (data.nodes && data.links) {
          setGraphData(data);
        }
      } catch (err) {
        console.error("Failed to parse JSON", err);
      }
    };
    reader.readAsText(file);
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
  };


  const handleCompareImport = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = event.target.files;
    if (!files || files.length !== 2) {
      alert("Please select exactly two JSON files to compare.");
      return;
    }

    const readFile = (file: File): Promise<any> => {
      return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = (e) => {
          try {
            resolve(JSON.parse(e.target?.result as string));
          } catch (err) {
            reject(err);
          }
        };
        reader.onerror = reject;
        reader.readAsText(file);
      });
    };

    try {
      const data1 = await readFile(files[0]);
      const data2 = await readFile(files[1]);
      
      const nodes1 = new Map(data1.nodes.map((n: any) => [n.id, n]));
      const nodes2 = new Map(data2.nodes.map((n: any) => [n.id, n]));
      
      const diffNodes: Node[] = [];
      
      nodes1.forEach((n: any, id: string) => {
        if (nodes2.has(id)) {
          diffNodes.push({ ...(nodes2.get(id) as any), diffStatus: 'unchanged' });
        } else {
          diffNodes.push({ ...n, diffStatus: 'removed' });
        }
      });
      
      nodes2.forEach((n: any, id: string) => {
        if (!nodes1.has(id)) {
          diffNodes.push({ ...n, diffStatus: 'added' });
        }
      });
      
      const getLinkId = (l: any) => {
        const sourceId = typeof l.source === 'object' && l.source !== null ? (l.source as any).id : l.source;
        const targetId = typeof l.target === 'object' && l.target !== null ? (l.target as any).id : l.target;
        return `${sourceId}->${targetId}`;
      };
      
      const links1 = new Map(data1.links.map((l: any) => [getLinkId(l), l]));
      const links2 = new Map(data2.links.map((l: any) => [getLinkId(l), l]));
      
      const diffLinks: any[] = [];
      
      links1.forEach((l: any, id: string) => {
        if (links2.has(id)) {
          diffLinks.push({ ...(links2.get(id) as any), diffStatus: 'unchanged' });
        } else {
          diffLinks.push({ ...l, diffStatus: 'removed' });
        }
      });
      
      links2.forEach((l: any, id: string) => {
        if (!links1.has(id)) {
          diffLinks.push({ ...l, diffStatus: 'added' });
        }
      });
      
      setGraphData({ nodes: diffNodes, links: diffLinks });
      setSimulationKey(prev => prev + 1);
    } catch (err) {
      console.error("Failed to parse JSON for comparison", err);
      alert("Failed to read files. Make sure they are valid exported JSON state files.");
    }

    if (compareInputRef.current) {
      compareInputRef.current.value = '';
    }
  };

  const handleBulkImport = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = event.target.files;
    if (!files || files.length === 0) return;

    const newNodesMap = new Map<string, Node>();
    const newLinks: any[] = [];

    const readFile = (file: File): Promise<string> => {
      return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = (e) => resolve(e.target?.result as string);
        reader.onerror = reject;
        reader.readAsText(file);
      });
    };

    try {
      for (let i = 0; i < files.length; i++) {
        const file = files[i];
        const content = await readFile(file);
        
        if (file.name.endsWith('.json')) {
          try {
            const parsed = JSON.parse(content);
            if (parsed.nodes) {
              parsed.nodes.forEach((n: any) => {
                if (!newNodesMap.has(n.id)) {
                  newNodesMap.set(n.id, { ...n });
                }
              });
            }
            if (parsed.links) {
              parsed.links.forEach((l: any) => newLinks.push({ ...l }));
            }
          } catch (e) {
            console.warn(`Failed to parse JSON file: ${file.name}`);
          }
        } else if (file.name.endsWith('.md')) {
          const lines = content.split('\n');
          let title = file.name.replace('.md', '');
          const titleLine = lines.find(l => l.startsWith('# '));
          if (titleLine) {
            title = titleLine.replace('# ', '').trim();
          }
          
          if (!newNodesMap.has(title)) {
            newNodesMap.set(title, { id: title, type: 'Research' });
          }

          const wikiLinkRegex = /\[\[(.*?)\]\]/g;
          let match;
          while ((match = wikiLinkRegex.exec(content)) !== null) {
            const targetId = match[1];
            if (!newNodesMap.has(targetId)) {
              newNodesMap.set(targetId, { id: targetId, type: 'Data' });
            }
            newLinks.push({
              source: title,
              target: targetId,
              value: 1,
              label: 'References'
            });
          }
        }
      }

      if (newNodesMap.size > 0 || newLinks.length > 0) {
        setGraphData(prev => {
          const combinedNodesMap = new Map<string, Node>();
          prev.nodes.forEach(n => combinedNodesMap.set(n.id, n));
          newNodesMap.forEach(n => combinedNodesMap.set(n.id, n));
          
          return {
            nodes: Array.from(combinedNodesMap.values()),
            links: [...prev.links, ...newLinks]
          };
        });
        setSimulationKey(prev => prev + 1);
      }
    } catch (error) {
      console.error('Bulk import failed:', error);
      alert('Failed to import some files.');
    }

    if (bulkImportRef.current) {
      bulkImportRef.current.value = '';
    }
  };

  useEffect(() => {
    if (!svgRef.current || dimensions.width === 0 || dimensions.height === 0) return;

    const width = dimensions.width;
    const height = dimensions.height;

    if (graphData.nodes.length === 0) {
      d3.select(svgRef.current).selectAll('*').remove();
      return;
    }

    const svg = d3.select(svgRef.current)
      .attr('width', width)
      .attr('height', height)
      .on('click', () => {
        setSelectedNodeId(null);
        setSelectedNodeIds([]);
        setHighlightedPath(null);
      }) as d3.Selection<SVGSVGElement, unknown, null, undefined>;
      
    svgSelectionRef.current = svg;

    // Add SVG defs for glow effect
    if (svg.select('defs').empty()) {
      const defs = svg.append('defs');
      
      const filter = defs.append('filter')
        .attr('id', 'glow')
        .attr('x', '-50%')
        .attr('y', '-50%')
        .attr('width', '200%')
        .attr('height', '200%');
        
      filter.append('feGaussianBlur')
        .attr('stdDeviation', '4')
        .attr('result', 'coloredBlur');
        
      const feMerge = filter.append('feMerge');
      feMerge.append('feMergeNode').attr('in', 'coloredBlur');
      feMerge.append('feMergeNode').attr('in', 'SourceGraphic');
    }

    let g = svg.select<SVGGElement>('g.main-group');
    if (g.empty()) {
      g = svg.append('g').attr('class', 'main-group');
      const zoom = d3.zoom()
        .scaleExtent([0.1, 4])
        .on('zoom', (event) => {
          g.attr('transform', event.transform);
          
          // Update minimap viewport
          if (miniSvgRef.current && svgRef.current) {
            const miniSvg = d3.select(miniSvgRef.current);
            const t = event.transform;
            miniSvg.select('.mini-viewport')
              .attr('x', -t.x / t.k)
              .attr('y', -t.y / t.k)
              .attr('width', width / t.k)
              .attr('height', height / t.k);
          }
        });
      svg.call(zoom as any);
      zoomRef.current = zoom as any;
      
      g.append('g').attr('class', 'links-layer');
      g.append('g').attr('class', 'lock-layer');
      g.append('g').attr('class', 'nodes-layer');
      g.append('g').attr('class', 'labels-layer');
      g.append('g').attr('class', 'edge-labels-layer');
    }

    // Minimap setup
    const miniSvg = d3.select(miniSvgRef.current);
    if (miniSvg.select('.mini-main-group').empty()) {
      const miniG = miniSvg.append('g').attr('class', 'mini-main-group');
      miniG.append('g').attr('class', 'mini-links-layer');
      miniG.append('g').attr('class', 'mini-nodes-layer');
      miniSvg.append('rect')
        .attr('class', 'mini-viewport')
        .attr('fill', 'rgba(255,255,255,0.05)')
        .attr('stroke', 'var(--color-primary)')
        .attr('stroke-width', 2)
        .attr('vector-effect', 'non-scaling-stroke')
        .attr('cursor', 'move')
        .call(d3.drag()
          .on('drag', (event) => {
            if (!zoomRef.current || !svgSelectionRef.current) return;
            const t = d3.zoomTransform(svgSelectionRef.current.node() as any);
            const dx = -event.dx * t.k;
            const dy = -event.dy * t.k;
            svgSelectionRef.current.call(zoomRef.current.translateBy as any, dx / t.k, dy / t.k);
          }) as any
        );
    }

    const prevStates = nodeStateRef.current;
    const nodes = graphData.nodes.map(d => {
      const prevState = prevStates.get(d.id);
      if (prevState && !d.isLocked) {
        return { ...d, x: prevState.x, y: prevState.y, vx: prevState.vx, vy: prevState.vy };
      }
      return { ...d };
    });
    const links = graphData.links.map(d => ({ ...d }));

    const nodeDegrees: Record<string, number> = {};
    nodes.forEach((n: any) => nodeDegrees[n.id] = 0);
    links.forEach((l: any) => {
      const sourceId = typeof l.source === 'object' && l.source !== null ? (l.source as any).id : l.source;
      const targetId = typeof l.target === 'object' && l.target !== null ? (l.target as any).id : l.target;
      if (nodeDegrees[sourceId] !== undefined) nodeDegrees[sourceId]++;
      if (nodeDegrees[targetId] !== undefined) nodeDegrees[targetId]++;
    });

    const getRadius = (d: any) => {
      const degree = nodeDegrees[d.id] || 0;
      const baseRadius = d.id === 'EROS Core' ? 14 : 6;
      return baseRadius + Math.min(degree * 1.5, 16);
    };
    const effectiveLayout = layoutPreset === 'Adaptive' 
      ? (nodes.length > 15 ? 'Radial' : 'Force-Directed')
      : layoutPreset;

    const simulation = d3.forceSimulation(nodes)
      .force('link', d3.forceLink(links).id((d: any) => d.id).distance((d: any) => {
        const sourceId = typeof d.source === 'object' && d.source !== null ? (d.source as any).id : d.source;
        const targetId = typeof d.target === 'object' && d.target !== null ? (d.target as any).id : d.target;
        return physicsLinkDistance + (nodeDegrees[sourceId] || 0) * 3 + (nodeDegrees[targetId] || 0) * 3;
      }))
      .force('charge', d3.forceManyBody().strength((d: any) => -physicsRepulsion - (nodeDegrees[d.id] || 0) * 20))
      .force('collision', d3.forceCollide().radius((d: any) => getRadius(d) + 25).iterations(3));

    if (effectiveLayout === 'Force-Directed') {
      simulation.force('center', d3.forceCenter(width / 2, height / 2));
    } else if (effectiveLayout === 'Radial') {
      simulation
        .force('radial', d3.forceRadial(Math.min(width, height) / 3, width / 2, height / 2).strength(0.8))
        .force('charge', d3.forceManyBody().strength(-50));
    } else if (effectiveLayout === 'Concentric') {
      simulation
        .force('radial', d3.forceRadial((d: any) => {
          const group = categorizeNode(d);
          return (group * Math.min(width, height) / 16) + 40;
        }, width / 2, height / 2).strength(1))
        .force('charge', d3.forceManyBody().strength(-80))
        .force('collide', d3.forceCollide().radius((d: any) => getRadius(d) + 15).iterations(3));
    } else if (effectiveLayout === 'Hierarchical' || effectiveLayout === 'Tree') {
      simulation
        .force('y', d3.forceY().y((d: any) => {
          const group = categorizeNode(d);
          return (group * (height / 8)) + (height * 0.15);
        }).strength(0.8))
        .force('x', d3.forceX(width / 2).strength(effectiveLayout === 'Tree' ? 0.05 : 0.1))
        .force('charge', d3.forceManyBody().strength(effectiveLayout === 'Tree' ? -150 : -100));
    }

    if (isClusterAll && effectiveLayout === 'Force-Directed') {
      const clusterCenters: Record<number, { x: number, y: number }> = {
        1: { x: width * 0.25, y: height * 0.25 },
        2: { x: width * 0.75, y: height * 0.25 },
        3: { x: width * 0.25, y: height * 0.75 },
        4: { x: width * 0.75, y: height * 0.75 }
      };

      simulation
        .force('x', d3.forceX().x((d: any) => {
          const group = categorizeNode(d);
          return clusterCenters[group]?.x || width / 2;
        }).strength(0.1))
        .force('y', d3.forceY().y((d: any) => {
          const group = categorizeNode(d);
          return clusterCenters[group]?.y || height / 2;
        }).strength(0.1));
    }

        let neighborIds = new Set<string>();
    if (isHighlightNeighbors && selectedNodeId) {
      links.forEach((l: any) => {
        const sourceId = typeof l.source === 'object' && l.source !== null ? (l.source as any).id : l.source;
        const targetId = typeof l.target === 'object' && l.target !== null ? (l.target as any).id : l.target;
        if (sourceId === selectedNodeId) neighborIds.add(targetId);
        if (targetId === selectedNodeId) neighborIds.add(sourceId);
      });
      neighborIds.add(selectedNodeId);
    }
    
    if (showTransparency && hoverNode) {
      links.forEach((l: any) => {
        const sourceId = typeof l.source === 'object' && l.source !== null ? (l.source as any).id : l.source;
        const targetId = typeof l.target === 'object' && l.target !== null ? (l.target as any).id : l.target;
        if (sourceId === hoverNode.node.id) neighborIds.add(targetId);
        if (targetId === hoverNode.node.id) neighborIds.add(sourceId);
      });
      neighborIds.add(hoverNode.node.id);
    }

    const link = g.select('.links-layer')
      .selectAll('path')
      .data(links, (d: any) => `${typeof d.source === 'object' ? d.source.id : d.source}-${typeof d.target === 'object' ? d.target.id : d.target}`)
      .join(
        enter => enter.append('path')
          .attr('opacity', 0)
          .call(e => e.transition().duration(500).attr('opacity', 1)),
        update => update,
        exit => exit.transition().duration(500).attr('opacity', 0).remove()
      )
      .attr('fill', 'none')
      .attr('class', (d: any) => {
        const sourceId = typeof d.source === 'object' && d.source !== null ? (d.source as any).id : d.source;
        const targetId = typeof d.target === 'object' && d.target !== null ? (d.target as any).id : d.target;
        return highlightedPath?.has(`${sourceId}-${targetId}`) || highlightedPath?.has(`${targetId}-${sourceId}`) 
          ? 'highlighted-path-link' 
          : '';
      })
            .attr('stroke', (d: any) => {
        if (d.diffStatus === 'added') return '#22c55e';
        if (d.diffStatus === 'removed') return '#ef4444';
        const sourceId = typeof d.source === 'object' && d.source !== null ? (d.source as any).id : d.source;
        const targetId = typeof d.target === 'object' && d.target !== null ? (d.target as any).id : d.target;
        if (isHighlightNeighbors && selectedNodeId && (sourceId === selectedNodeId || targetId === selectedNodeId)) {
          return 'var(--color-primary)';
        }
        return 'var(--color-border-main)';
      })
      .attr('filter', (d: any) => {
        const sourceId = typeof d.source === 'object' && d.source !== null ? (d.source as any).id : d.source;
        const targetId = typeof d.target === 'object' && d.target !== null ? (d.target as any).id : d.target;
        if (highlightedPath?.has(`${sourceId}-${targetId}`) || highlightedPath?.has(`${targetId}-${sourceId}`)) {
          return 'url(#glow)';
        }
        if (isHighlightNeighbors && selectedNodeId && (sourceId === selectedNodeId || targetId === selectedNodeId)) {
          return 'url(#glow)';
        }
        return null;
      })
      .attr('stroke-dasharray', (d: any) => d.diffStatus === 'removed' ? '4 2' : null)
      .attr('stroke-width', (d: any) => {
        if (d.diffStatus === 'added' || d.diffStatus === 'removed') return 3;
        return Math.max(1, Math.min(Math.sqrt(d.value || 1) * 1.5, 8));
      })
      .attr('stroke-opacity', (d: any) => {
        const sourceId = typeof d.source === 'object' && d.source !== null ? (d.source as any).id : d.source;
        const targetId = typeof d.target === 'object' && d.target !== null ? (d.target as any).id : d.target;
        if (highlightedPath?.has(`${sourceId}-${targetId}`) || highlightedPath?.has(`${targetId}-${sourceId}`)) {
          return 1;
        }
        return Math.max(0.2, Math.min(0.2 + ((d.value || 1) * 0.15), 0.9));
      });
      
    link.append('title')
      .text(d => d.label || `${typeof d.source === 'object' ? (d.source as Node).id : d.source} -> ${typeof d.target === 'object' ? (d.target as Node).id : d.target}`);


    const lockIndicator = g.select('.lock-layer')
      .selectAll('circle')
      .data(nodes.filter(n => n.isLocked), (d: any) => d.id)
      .join(
        enter => enter.append('circle')
          .attr('class', 'agent-locked-node')
          .attr('fill', 'none')
          .attr('stroke', 'var(--color-accent-1)')
          .attr('r', 0)
          .call(e => e.transition().duration(500).attr('r', getRadius)),
        update => update,
        exit => exit.transition().duration(500).attr('r', 0).remove()
      );

    const node = g.select('.nodes-layer')
      .selectAll('circle')
      .data(nodes, (d: any) => d.id)
      .join(
        enter => enter.append('circle')
          .attr('r', 0)
          .attr('stroke', 'var(--color-bg-main)')
          .attr('stroke-width', 2)
          .call(e => e.transition().duration(500).attr('r', getRadius)),
        update => update.call(e => e.transition().duration(500).attr('r', getRadius)),
        exit => exit.transition().duration(500).attr('r', 0).remove()
      )
      .attr('fill', d => getNodeColor(d))
            .attr('stroke', (d: any) => {
        if (d.diffStatus === 'added') return '#22c55e';
        if (d.diffStatus === 'removed') return '#ef4444';
        if (selectedNodeIds.includes(d.id)) return 'var(--color-primary)';
        if (d.id === selectedNodeId) return 'var(--color-primary)';
        if (isHighlightNeighbors && selectedNodeId && neighborIds.has(d.id)) return 'var(--color-primary)';
        return 'var(--color-bg-main)';
      })
      .attr('stroke-dasharray', (d: any) => d.diffStatus === 'removed' ? '4 2' : null)
      .attr('opacity', (d: any) => d.diffStatus === 'removed' ? 0.5 : 1)
            .attr('stroke-width', (d: any) => {
        if (d.diffStatus === 'added' || d.diffStatus === 'removed') return 4;
        if (selectedNodeIds.includes(d.id)) return 3;
        if (d.id === selectedNodeId) return 3;
        if (isHighlightNeighbors && selectedNodeId && neighborIds.has(d.id)) return 3;
        return 2;
      })
      .attr('cursor', 'grab')
      .on('click', (event: any, d: any) => {
        event.stopPropagation();
        setSelectedNodeId(d.id);
        if (event.shiftKey || event.metaKey || event.ctrlKey) {
          setSelectedNodeIds(prev => prev.includes(d.id) ? prev.filter(id => id !== d.id) : [...prev, d.id]);
        } else {
          setSelectedNodeIds([d.id]);
        }
      })
      .on('contextmenu', (event: any, d: any) => {
        event.preventDefault();
        setContextMenu({
          x: event.clientX,
          y: event.clientY,
          nodeId: d.id
        });
      })
      .on('dblclick', (event: any, d: any) => {
        d.fx = null;
        d.fy = null;
        simulation.alphaTarget(0.3).restart();
      })
      .on('mouseover', (event: any, d: any) => {
        setHoverNode({
          x: event.clientX,
          y: event.clientY,
          node: d
        });
      })
      .on('mouseout', () => {
        setHoverNode(null);
      })
      .call(d3.drag<SVGCircleElement, Node>()
        .on('start', dragstarted)
        .on('drag', dragged)
        .on('end', dragended) as any);

    const labels = g.select('.labels-layer')
      .selectAll('text')
      .data(nodes, (d: any) => d.id)
      .join(
        enter => enter.append('text')
          .attr('opacity', 0)
          .call(e => e.transition().duration(500).attr('opacity', 1)),
        update => update,
        exit => exit.transition().duration(500).attr('opacity', 0).remove()
      )
      .attr('dx', d => getRadius(d) + 5)
      .attr('dy', 4)
      .text(d => d.id)
      .attr('font-size', '10px')
      .attr('font-family', '"JetBrains Mono", monospace')
      .attr('fill', 'var(--color-text-secondary)')
      .attr('display', showLabels ? 'block' : 'none');

    const edgeLabels = g.select('.edge-labels-layer')
      .selectAll('text')
      .data(links, (d: any) => `${typeof d.source === 'object' ? d.source.id : d.source}-${typeof d.target === 'object' ? d.target.id : d.target}`)
      .join(
        enter => enter.append('text')
          .attr('opacity', 0)
          .call(e => e.transition().duration(500).attr('opacity', 1)),
        update => update,
        exit => exit.transition().duration(500).attr('opacity', 0).remove()
      )
      .attr('font-size', '8px')
      .attr('font-family', '"JetBrains Mono", monospace')
      .attr('fill', 'var(--color-text-muted)')
      .attr('text-anchor', 'middle')
      .attr('display', showTransparency ? 'block' : 'none')
      .text((d: any) => {
        const parts = [];
        if (d.label) parts.push(d.label);
        if (d.agent) parts.push(`[${d.agent}]`);
        return parts.join(' ');
      });

    const miniLink = miniSvg.select('.mini-links-layer')
      .selectAll('line')
      .data(links, (d: any) => `${typeof d.source === 'object' ? d.source.id : d.source}-${typeof d.target === 'object' ? d.target.id : d.target}`)
      .join('line')
      .attr('stroke', 'var(--color-border-main)')
      .attr('stroke-width', 1)
      .attr('opacity', 0.5);

    const miniNode = miniSvg.select('.mini-nodes-layer')
      .selectAll('circle')
      .data(nodes, (d: any) => d.id)
      .join('circle')
      .attr('r', (d: any) => getRadius(d))
      .attr('fill', d => getNodeColor(d))
      .attr('stroke', 'var(--color-bg-main)')
      .attr('stroke-width', 1);


    simulation.on('tick', () => {
      link.attr('d', (d: any) => {
        const dx = d.target.x - d.source.x;
        const dy = d.target.y - d.source.y;
        const cx = (d.source.x + d.target.x) / 2 - dy * 0.2;
        const cy = (d.source.y + d.target.y) / 2 + dx * 0.2;
        return `M${d.source.x},${d.source.y} Q${cx},${cy} ${d.target.x},${d.target.y}`;
      });

      node
        .attr('cx', (d: any) => d.x)
        .attr('cy', (d: any) => d.y);
        
      lockIndicator
        .attr('cx', (d: any) => d.x)
        .attr('cy', (d: any) => d.y);

      labels
        .attr('x', (d: any) => d.x)
        .attr('y', (d: any) => d.y);

      edgeLabels
        .attr('x', (d: any) => {
          const dx = d.target.x - d.source.x;
          const dy = d.target.y - d.source.y;
          return (d.source.x + d.target.x) / 2 - dy * 0.2;
        })
        .attr('y', (d: any) => {
          const dx = d.target.x - d.source.x;
          const dy = d.target.y - d.source.y;
          return (d.source.y + d.target.y) / 2 + dx * 0.2 - 5;
        });
    });

    function dragstarted(event: any) {
      if (!event.active) simulation.alphaTarget(0.3).restart();
      event.subject.fx = event.subject.x;
      event.subject.fy = event.subject.y;
    }

    function dragged(event: any) {
      if (isSnapToGrid) {
        const gridSize = 40;
        event.subject.fx = Math.round(event.x / gridSize) * gridSize;
        event.subject.fy = Math.round(event.y / gridSize) * gridSize;
      } else {
        event.subject.fx = event.x;
        event.subject.fy = event.y;
      }
    }

    function dragended(event: any) {
      if (!event.active) simulation.alphaTarget(0);
      // Removed setting fx and fy to null to keep nodes pinned where they are dragged
    }

    return () => {
      simulation.stop();
      nodes.forEach((n: any) => {
        if (n.x !== undefined && n.y !== undefined) {
          nodeStateRef.current.set(n.id, { x: n.x, y: n.y, vx: n.vx, vy: n.vy });
        }
      });
    };
  }, [dimensions, graphData, simulationKey, isClusterAll, isSnapToGrid, selectedNodeId, layoutPreset, highlightedPath, isHighlightNeighbors, showLabels, selectedNodeIds, physicsRepulsion, physicsLinkDistance]);

  return (
    <div className="flex flex-col h-full bg-[var(--color-bg-main)] border-l border-[var(--color-border-subtle)]">
      <header className="px-6 py-4 border-b border-[var(--color-border-subtle)] bg-[var(--color-bg-surface)] flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Network size={16} className="text-[var(--color-primary)]" />
          <h2 className="text-sm font-bold tracking-widest text-white uppercase font-sans">Knowledge Graph</h2>
          {selectionHistory.length > 0 && (
            <div className="flex items-center gap-2 ml-4 pl-4 border-l border-[var(--color-border-main)] text-xs text-[var(--color-text-muted)] font-mono">
              {selectionHistory.map((nodeId, idx) => (
                <React.Fragment key={idx}>
                  {idx > 0 && <ChevronRight size={12} className="opacity-50" />}
                  <button 
                    onClick={() => setSelectedNodeId(nodeId)}
                    className={`hover:text-white transition-colors truncate max-w-[120px] ${selectedNodeId === nodeId ? 'text-[var(--color-primary)] font-bold' : ''}`}
                    title={nodeId}
                  >
                    {nodeId}
                  </button>
                </React.Fragment>
              ))}
            </div>
          )}
        </div>
        <div className="flex items-center gap-6">
          <div className="flex items-center gap-3 border-r border-[var(--color-border-main)] pr-6">
            <input 
              type="text" 
              placeholder="Query Graph (e.g., 'find all agents')"
              value={graphQuery}
              onChange={(e) => setGraphQuery(e.target.value)}
              onKeyDown={handleQueryGraph}
              disabled={isQuerying}
              className="bg-[var(--color-bg-surface-hover)] border border-[var(--color-border-main)] text-[var(--color-text-main)] text-[10px] tracking-wide px-3 py-1.5 min-w-[220px] focus:outline-none focus:border-[var(--color-primary)] placeholder-[var(--color-text-muted)]"
            />
            <button
              onClick={toggleMic}
              disabled={isQuerying}
              className={`p-1.5 border transition-colors ${
                isListening 
                  ? 'bg-red-500/20 text-red-500 border-red-500/50 animate-pulse' 
                  : 'bg-[var(--color-bg-surface-hover)] border-[var(--color-border-main)] text-[var(--color-text-secondary)] hover:text-white hover:border-[var(--color-text-muted)]'
              }`}
              title="Voice Query"
            >
              {isListening ? <Mic size={14} /> : <MicOff size={14} />}
            </button>
            {isQuerying && <span className="text-[10px] text-[var(--color-primary)] animate-pulse uppercase tracking-widest font-bold">...</span>}
          </div>
          <div className="flex items-center gap-3 border-r border-[var(--color-border-main)] pr-6">
            <span className="text-[10px] text-[var(--color-text-muted)] tracking-widest uppercase font-bold">Layout</span>
            <select
              value={layoutPreset}
              onChange={(e) => setLayoutPreset(e.target.value as any)}
              className="bg-[var(--color-bg-surface-hover)] border border-[var(--color-border-main)] text-[var(--color-text-main)] text-[10px] uppercase tracking-widest px-2 py-1 focus:outline-none focus:border-[var(--color-primary)]"
            >
              <option value="Adaptive">Adaptive</option>
              <option value="Force-Directed">Force-Directed</option>
              <option value="Radial">Radial</option>
              <option value="Tree">Tree</option>
              <option value="Hierarchical">Hierarchical</option>
            </select>
          </div>
          <div className="flex items-center gap-3 border-r border-[var(--color-border-main)] pr-6">
            <span className="text-[10px] text-[var(--color-text-muted)] tracking-widest uppercase font-bold">Data Management</span>
            <input 
              type="file" 
              accept=".json" 
              ref={fileInputRef}
              onChange={handleImport}
              className="hidden"
            />
            <input 
              type="file" 
              {...({ webkitdirectory: "", directory: "" } as any)}
              ref={bulkImportRef}
              onChange={handleBulkImport}
              className="hidden"
            />
            <input 
              type="file" 
              multiple
              accept=".json"
              ref={compareInputRef}
              onChange={handleCompareImport}
              className="hidden"
            />
            <button
              onClick={() => fileInputRef.current?.click()}
              className="text-[10px] tracking-widest uppercase border border-[var(--color-border-main)] px-3 py-1 text-[var(--color-text-secondary)] hover:text-[var(--color-text-bright)] hover:border-[var(--color-text-bright)] transition-colors"
            >
              Import
            </button>
            <button
              onClick={() => bulkImportRef.current?.click()}
              className="text-[10px] tracking-widest uppercase border border-[var(--color-border-main)] px-3 py-1 text-[var(--color-text-secondary)] hover:text-[var(--color-text-bright)] hover:border-[var(--color-text-bright)] transition-colors"
            >
              Bulk Import
            </button>
            <button
              onClick={() => compareInputRef.current?.click()}
              className="text-[10px] tracking-widest uppercase border border-[var(--color-primary)] px-3 py-1 text-[var(--color-primary)] hover:bg-[var(--color-primary)]/10 transition-colors"
            >
              Compare Graphs
            </button>
                        <button
              onClick={handleExport}
              className="text-[10px] tracking-widest uppercase border border-[var(--color-border-main)] px-3 py-1 text-[var(--color-text-secondary)] hover:text-[var(--color-text-bright)] hover:border-[var(--color-text-bright)] transition-colors"
            >
              Download JSON
            </button>
            <button
              onClick={handleExportSVG}
              className="text-[10px] tracking-widest uppercase border border-[var(--color-border-main)] px-3 py-1 text-[var(--color-text-secondary)] hover:text-[var(--color-text-bright)] hover:border-[var(--color-text-bright)] transition-colors"
            >
              Download SVG
            </button>
            <button
              onClick={handleExportMermaid}
              className="text-[10px] tracking-widest uppercase border border-[var(--color-border-main)] px-3 py-1 text-[var(--color-text-secondary)] hover:text-[var(--color-text-bright)] hover:border-[var(--color-text-bright)] transition-colors"
            >
              Download Mermaid
            </button>
          </div>
          <div className="flex items-center gap-2">
                                    <button
              onClick={() => setShowLabels(!showLabels)}
              className={`text-[10px] tracking-widest uppercase border px-3 py-1 transition-colors ${
                showLabels 
                  ? 'border-[var(--color-primary)] text-[var(--color-primary)] bg-[var(--color-primary)]/10' 
                  : 'border-[var(--color-border-main)] text-[var(--color-text-secondary)] hover:text-[var(--color-primary)] hover:border-[var(--color-primary)]'
              }`}
            >
              Show Labels
            </button>
            <button
              onClick={() => setShowTransparency(!showTransparency)}
              className={`text-[10px] tracking-widest uppercase border px-3 py-1 transition-colors ${
                showTransparency 
                  ? 'border-[var(--color-primary)] text-[var(--color-primary)] bg-[var(--color-primary)]/10' 
                  : 'border-[var(--color-border-main)] text-[var(--color-text-secondary)] hover:text-[var(--color-primary)] hover:border-[var(--color-primary)]'
              }`}
            >
              Transparency
            </button>
            <button
              onClick={() => setIsHighlightNeighbors(!isHighlightNeighbors)}
              className={`text-[10px] tracking-widest uppercase border px-3 py-1 transition-colors ${
                isHighlightNeighbors 
                  ? 'border-[var(--color-primary)] text-[var(--color-primary)] bg-[var(--color-primary)]/10' 
                  : 'border-[var(--color-border-main)] text-[var(--color-text-secondary)] hover:text-[var(--color-primary)] hover:border-[var(--color-primary)]'
              }`}
            >
              Highlight Neighbors
            </button>
            <button
              onClick={() => setIsSnapToGrid(!isSnapToGrid)}
              className={`text-[10px] tracking-widest uppercase border px-3 py-1 transition-colors ${
                isSnapToGrid 
                  ? 'border-[var(--color-primary)] text-[var(--color-primary)] bg-[var(--color-primary)]/10' 
                  : 'border-[var(--color-border-main)] text-[var(--color-text-secondary)] hover:text-[var(--color-primary)] hover:border-[var(--color-primary)]'
              }`}
            >
              Snap to Grid
            </button>
            <button
              onClick={() => setIsClusterAll(!isClusterAll)}
              className={`text-[10px] tracking-widest uppercase border px-3 py-1 transition-colors ${
                isClusterAll 
                  ? 'border-[var(--color-primary)] text-[var(--color-primary)] bg-[var(--color-primary)]/10' 
                  : 'border-[var(--color-border-main)] text-[var(--color-text-secondary)] hover:text-[var(--color-primary)] hover:border-[var(--color-primary)]'
              }`}
            >
              Cluster All
            </button>
            <button
              onClick={handleZoomIn}
              className="text-[10px] tracking-widest uppercase border border-[var(--color-border-main)] px-3 py-1 text-[var(--color-text-secondary)] hover:text-[var(--color-primary)] hover:border-[var(--color-primary)] transition-colors"
            >
              Zoom In
            </button>
            <button
              onClick={handleZoomOut}
              className="text-[10px] tracking-widest uppercase border border-[var(--color-border-main)] px-3 py-1 text-[var(--color-text-secondary)] hover:text-[var(--color-primary)] hover:border-[var(--color-primary)] transition-colors"
            >
              Zoom Out
            </button>
            <button
              onClick={handleZoomToFit}
              className="text-[10px] tracking-widest uppercase border border-[var(--color-border-main)] px-3 py-1 text-[var(--color-text-secondary)] hover:text-[var(--color-primary)] hover:border-[var(--color-primary)] transition-colors"
            >
              Zoom to Fit
            </button>
            <button
              onClick={handleForceSimulation}
              className="text-[10px] tracking-widest uppercase border border-[var(--color-border-main)] px-3 py-1 text-[var(--color-text-secondary)] hover:text-[var(--color-primary)] hover:border-[var(--color-primary)] transition-colors"
            >
              Force Simulation
            </button>
            <div className="relative">
              <button
                onClick={() => setIsSettingsOpen(!isSettingsOpen)}
                className={`flex items-center justify-center p-1 border transition-colors ${
                  isSettingsOpen 
                    ? 'border-[var(--color-primary)] text-[var(--color-primary)] bg-[var(--color-primary)]/10' 
                    : 'border-[var(--color-border-main)] text-[var(--color-text-secondary)] hover:text-[var(--color-primary)] hover:border-[var(--color-primary)]'
                }`}
              >
                <Settings size={16} />
              </button>
              {isSettingsOpen && (
                <div className="absolute right-0 top-full mt-2 w-64 bg-[var(--color-bg-surface)] border border-[var(--color-border-main)] shadow-xl z-50 p-4">
                  <h3 className="text-xs font-bold text-white uppercase tracking-widest mb-4 border-b border-[var(--color-border-subtle)] pb-2">Physics Settings</h3>
                  
                  <div className="space-y-4">
                    <div className="flex flex-col gap-2">
                      <div className="flex justify-between items-center text-[10px] uppercase tracking-widest text-[var(--color-text-secondary)]">
                        <span>Repulsion</span>
                        <span className="text-[var(--color-primary)] font-mono">{physicsRepulsion}</span>
                      </div>
                      <input 
                        type="range" 
                        min="50" max="1000" step="10" 
                        value={physicsRepulsion} 
                        onChange={(e) => setPhysicsRepulsion(parseInt(e.target.value))}
                        className="w-full accent-[var(--color-primary)]"
                      />
                    </div>
                    
                    <div className="flex flex-col gap-2">
                      <div className="flex justify-between items-center text-[10px] uppercase tracking-widest text-[var(--color-text-secondary)]">
                        <span>Link Distance</span>
                        <span className="text-[var(--color-primary)] font-mono">{physicsLinkDistance}</span>
                      </div>
                      <input 
                        type="range" 
                        min="10" max="300" step="5" 
                        value={physicsLinkDistance} 
                        onChange={(e) => setPhysicsLinkDistance(parseInt(e.target.value))}
                        className="w-full accent-[var(--color-primary)]"
                      />
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      </header>
      <div className="flex-1 overflow-hidden relative" ref={containerRef}>
        <svg ref={svgRef} className="w-full h-full cursor-crosshair" />
        
        {/* Minimap */}
        <div className="absolute bottom-4 right-4 w-48 h-32 bg-[var(--color-bg-surface)]/80 backdrop-blur-sm border border-[var(--color-border-subtle)] z-10 shadow-lg">
          <svg ref={miniSvgRef} className="w-full h-full" />
        </div>
        
        {/* Stats Overlay */}
        <div className="absolute top-4 left-4 bg-[var(--color-bg-surface)]/80 backdrop-blur-sm border border-[var(--color-border-subtle)] p-3 z-10 pointer-events-none">
          <div className="flex flex-col gap-1 text-[10px] uppercase tracking-widest text-[var(--color-text-secondary)] font-mono">
            <div className="flex justify-between gap-4">
              <span>Nodes:</span>
              <span className="text-[var(--color-primary)] font-bold">{graphData.nodes.length}</span>
            </div>
            <div className="flex justify-between gap-4">
              <span>Edges:</span>
              <span className="text-[var(--color-primary)] font-bold">{graphData.links.length}</span>
            </div>
          </div>
        </div>

        {/* Bulk Action Menu */}
        {selectedNodeIds.length > 1 && (
          <div className="absolute top-4 left-1/2 -translate-x-1/2 bg-[var(--color-bg-surface)] border border-[var(--color-border-main)] p-2 shadow-xl z-20 flex items-center gap-2">
             <span className="text-xs font-mono text-white px-2 border-r border-[var(--color-border-subtle)]">
               {selectedNodeIds.length} Selected
             </span>
             <button onClick={handleBulkDelete} className="text-xs text-[var(--color-accent-1)] hover:text-white transition-colors px-2">Delete Selection</button>
             <button onClick={handleBulkHighlight} className="text-xs text-[var(--color-primary)] hover:text-white transition-colors px-2">Highlight Group</button>
          </div>
        )}

        {/* Collapsible Legend */}
        <div className={`absolute bottom-4 left-4 bg-[var(--color-bg-surface)] border border-[var(--color-border-subtle)] transition-all duration-300 overflow-hidden flex flex-col z-10 ${isLegendOpen ? 'w-48 max-h-96' : 'w-24 h-8'}`}>
          <button 
            onClick={() => setIsLegendOpen(!isLegendOpen)} 
            className="w-full h-8 min-h-[32px] flex items-center justify-between px-3 text-[10px] uppercase tracking-widest text-[var(--color-text-secondary)] hover:text-white transition-colors bg-[var(--color-bg-surface-hover)] shrink-0"
          >
            <span>Legend</span>
            {isLegendOpen ? <ChevronDown size={12} /> : <ChevronUp size={12} />}
          </button>
          
          <div className={`p-4 space-y-4 text-[10px] text-[var(--color-text-secondary)] transition-opacity duration-300 ${isLegendOpen ? 'opacity-100' : 'opacity-0'}`}>
            <div>
              <h4 className="font-bold text-[var(--color-text-muted)] mb-2 uppercase tracking-widest">Node Types</h4>
              <div className="space-y-2">
                <div className="flex items-center gap-2">
                  <div className="w-2.5 h-2.5 rounded-full bg-[var(--color-primary)]"></div>
                  <span>Project</span>
                </div>
                <div className="flex items-center gap-2">
                  <div className="w-2.5 h-2.5 rounded-full bg-[var(--color-type-system)]"></div>
                  <span>System</span>
                </div>
                <div className="flex items-center gap-2">
                  <div className="w-2.5 h-2.5 rounded-full bg-[var(--color-type-component)]"></div>
                  <span>Component</span>
                </div>
                <div className="flex items-center gap-2">
                  <div className="w-2.5 h-2.5 rounded-full bg-[var(--color-type-agent)]"></div>
                  <span>Agent</span>
                </div>
                <div className="flex items-center gap-2">
                  <div className="w-2.5 h-2.5 rounded-full bg-[var(--color-type-logic)]"></div>
                  <span>Logic</span>
                </div>
                <div className="flex items-center gap-2">
                  <div className="w-2.5 h-2.5 rounded-full bg-[var(--color-type-data)]"></div>
                  <span>Data</span>
                </div>
                <div className="flex items-center gap-2">
                  <div className="w-2.5 h-2.5 rounded-full bg-[var(--color-type-action)]"></div>
                  <span>Action</span>
                </div>
              </div>
            </div>
            
            <div>
              <h4 className="font-bold text-[var(--color-text-muted)] mb-2 uppercase tracking-widest">Edge Types</h4>
              <div className="space-y-2">
                <div className="flex items-center gap-2">
                  <div className="w-4 h-[1px] bg-[var(--color-text-muted)]"></div>
                  <span>Standard Link</span>
                </div>
                <div className="flex items-center gap-2">
                  <div className="w-4 h-[2px] bg-[var(--color-text-secondary)]"></div>
                  <span>Primary Bus</span>
                </div>
              </div>
            </div>
          </div>
        </div>

        {hoverNode && !contextMenu && (
          <div 
            className="fixed bg-[var(--color-bg-surface)] border border-[var(--color-border-main)] shadow-xl z-50 p-3 max-w-[250px] pointer-events-none"
            style={{ top: hoverNode.y + 15, left: hoverNode.x + 15 }}
          >
            <h3 className="text-xs font-bold text-white uppercase tracking-widest mb-1">{hoverNode.node.id}</h3>
            <div className="text-[10px] text-[var(--color-text-secondary)] uppercase tracking-widest mb-2 border-b border-[var(--color-border-subtle)] pb-2">
              Type: {hoverNode.node.type || 'Data'}
            </div>
            <div className="text-xs text-[var(--color-text-main)]">
              {hoverNode.node.rationale 
                ? hoverNode.node.rationale 
                : `Entity representing ${hoverNode.node.id} in the knowledge graph. Select to view full details.`}
            </div>
          </div>
        )}

        {contextMenu && (
          <div 
            className="fixed bg-[var(--color-bg-surface-hover)] border border-[var(--color-border-main)] shadow-lg flex flex-col z-50 text-[10px] uppercase tracking-widest"
            style={{ top: contextMenu.y, left: contextMenu.x }}
          >
                        {selectedNodeIds.length > 1 && selectedNodeIds.includes(contextMenu.nodeId) && (
              <button 
                className="px-4 py-2 text-left text-[var(--color-primary)] hover:bg-[var(--color-border-subtle)] transition-colors"
                onClick={() => {
                  const superNodeId = prompt('Enter name for the grouped node:', `Group (${selectedNodeIds.length} nodes)`);
                  if (superNodeId) {
                    setGraphData(prev => {
                      const newNodes = prev.nodes.filter(n => !selectedNodeIds.includes(n.id));
                      const newLinks = prev.links.map(l => {
                        const s = typeof l.source === 'object' && l.source !== null ? (l.source as any).id : l.source;
                        const t = typeof l.target === 'object' && l.target !== null ? (l.target as any).id : l.target;
                        return {
                          ...l,
                          source: selectedNodeIds.includes(s) ? superNodeId : l.source,
                          target: selectedNodeIds.includes(t) ? superNodeId : l.target
                        };
                      }).filter(l => {
                        const s = typeof l.source === 'object' && l.source !== null ? (l.source as any).id : l.source;
                        const t = typeof l.target === 'object' && l.target !== null ? (l.target as any).id : l.target;
                        return s !== t;
                      });
                      
                      const uniqueLinks = newLinks.filter((l, index, self) =>
                        index === self.findIndex((t) => (
                          (typeof t.source === 'object' && t.source !== null ? (t.source as any).id : t.source) === (typeof l.source === 'object' && l.source !== null ? (l.source as any).id : l.source) && 
                          (typeof t.target === 'object' && t.target !== null ? (t.target as any).id : t.target) === (typeof l.target === 'object' && l.target !== null ? (l.target as any).id : l.target)
                        ))
                      );

                      newNodes.push({ id: superNodeId, group: 1 });
                      return { nodes: newNodes, links: uniqueLinks };
                    });
                    setSelectedNodeId(superNodeId);
                    setSelectedNodeIds([superNodeId]);
                    setSimulationKey(prev => prev + 1);
                  }
                  setContextMenu(null);
                }}
              >
                Group Selected Nodes
              </button>
            )}
            <button 
              className="px-4 py-2 text-left text-[var(--color-text-secondary)] hover:bg-[var(--color-border-subtle)] hover:text-white transition-colors"
              onClick={() => handleRename(contextMenu.nodeId)}
            >
              Rename
            </button>
            <button 
              className="px-4 py-2 text-left text-[var(--color-text-secondary)] hover:bg-[var(--color-border-subtle)] hover:text-white transition-colors"
              onClick={() => handleAddConnection(contextMenu.nodeId)}
            >
              Add Connection
            </button>
            {selectedNodeId && selectedNodeId !== contextMenu.nodeId && (
              <>
                <button 
                  className="px-4 py-2 text-left text-[var(--color-primary)] hover:bg-[var(--color-border-subtle)] transition-colors"
                  onClick={() => handleSmartLink(contextMenu.nodeId)}
                >
                  Smart Link
                </button>
                <button 
                  className="px-4 py-2 text-left text-[var(--color-primary)] hover:bg-[var(--color-border-subtle)] transition-colors"
                  onClick={() => handleFindConnection(contextMenu.nodeId)}
                >
                  Find Connection
                </button>
              </>
            )}
            <button 
              className="px-4 py-2 text-left text-[var(--color-text-secondary)] hover:bg-[var(--color-border-subtle)] hover:text-white transition-colors"
              onClick={() => handleSetPriority(contextMenu.nodeId)}
            >
              Set Priority
            </button>
            <button 
              className="px-4 py-2 text-left text-[var(--color-text-secondary)] hover:bg-[var(--color-border-subtle)] hover:text-white transition-colors"
              onClick={() => handleToggleLock(contextMenu.nodeId)}
            >
              {graphData.nodes.find(n => n.id === contextMenu.nodeId)?.isLocked ? 'Unlock Agent' : 'Lock Agent Process'}
            </button>
            <button 
              className="px-4 py-2 text-left text-[var(--color-accent-1)] hover:bg-[var(--color-border-subtle)] transition-colors"
              onClick={() => handleDelete(contextMenu.nodeId)}
            >
              Delete
            </button>
          </div>
        )}

        {selectedNodeId && selectedNodeIds.length <= 1 && (() => {
          const connectedLinks = graphData.links.filter(l => {
            const s = typeof l.source === 'object' && l.source !== null ? (l.source as any).id : l.source;
            const t = typeof l.target === 'object' && l.target !== null ? (l.target as any).id : l.target;
            return s === selectedNodeId || t === selectedNodeId;
          });
          const neighborsCount = connectedLinks.length;
          const centralityScore = graphData.nodes.length > 1 ? (neighborsCount / (graphData.nodes.length - 1)).toFixed(2) : '0.00';
          const nodeData = graphData.nodes.find(n => n.id === selectedNodeId);
          
          const handleSaveDescription = () => {
            if (!selectedNodeId) return;
            setGraphData(prev => {
              const nodes = prev.nodes.map(n => n.id === selectedNodeId ? { ...n, description: descriptionDraft } : n);
              return { ...prev, nodes };
            });
            setIsEditingDescription(false);
          };

          return (
            <div className="absolute top-4 right-4 bg-[var(--color-bg-surface)] border border-[var(--color-border-main)] shadow-xl z-10 w-[320px] flex flex-col max-h-[calc(100%-2rem)]">
              <div className="p-4 border-b border-[var(--color-border-subtle)] shrink-0 relative">
                <h3 className="text-sm font-bold text-white uppercase tracking-widest pr-4 truncate">{selectedNodeId}</h3>
                <button 
                  onClick={() => {
                    setSelectedNodeId(null);
                    setHighlightedPath(null);
                  }}
                  className="absolute top-4 right-4 text-[var(--color-text-muted)] hover:text-white transition-colors"
                >
                  ×
                </button>
              </div>

              <div className="p-4 overflow-y-auto space-y-4 text-[10px] text-[var(--color-text-secondary)] tracking-widest uppercase flex-1">
                <div className="space-y-2">
                  <div className="flex justify-between items-center">
                    <span>Type</span>
                    <span className="text-white font-mono">{nodeData?.type || 'Data'}</span>
                  </div>
                  <div className="flex justify-between items-center">
                    <span>Neighbors</span>
                    <span className="text-[var(--color-primary)] font-mono">{neighborsCount}</span>
                  </div>
                  <div className="flex justify-between items-center">
                    <span>Centrality</span>
                    <span className="text-[var(--color-accent-1)] font-mono">{centralityScore}</span>
                  </div>
                  <div className="flex justify-between items-center">
                    <span>Status</span>
                    <span className={nodeData?.isLocked ? "text-[var(--color-accent-1)] font-mono" : "text-[var(--color-primary)] font-mono"}>
                      {nodeData?.isLocked ? 'LOCKED' : 'ACTIVE'}
                    </span>
                  </div>
                </div>

                {connectedLinks.length > 0 && (
                  <div className="pt-2 border-t border-[var(--color-border-subtle)]">
                    <span className="block text-white mb-2">Connected Edges</span>
                    <div className="space-y-1 max-h-32 overflow-y-auto pr-1">
                      {connectedLinks.map((l, i) => {
                        const sId = typeof l.source === 'object' && l.source !== null ? (l.source as any).id : l.source;
                        const tId = typeof l.target === 'object' && l.target !== null ? (l.target as any).id : l.target;
                        const isSource = sId === selectedNodeId;
                        const otherNode = isSource ? tId : sId;
                        const label = (l as any).label || 'connected to';
                        return (
                          <div key={i} className="flex flex-col gap-0.5 bg-[var(--color-bg-main)] p-1.5 border border-[var(--color-border-main)]">
                            <span className="text-white truncate" title={otherNode as string}>{otherNode as string}</span>
                            <span className="text-[8px] text-[var(--color-text-muted)] italic normal-case tracking-normal truncate">{isSource ? '→ ' : '← '} {label}</span>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}

                <div className="pt-2 border-t border-[var(--color-border-subtle)]">
                   <div className="flex justify-between items-center mb-2">
                     <span className="text-white">Description</span>
                     {!isEditingDescription && (
                       <button 
                         onClick={() => {
                           setDescriptionDraft(nodeData?.description || '');
                           setIsEditingDescription(true);
                         }}
                         className="text-[var(--color-primary)] hover:text-white transition-colors"
                       >
                         Edit
                       </button>
                     )}
                   </div>
                   
                   {isEditingDescription ? (
                     <div className="space-y-2">
                       <textarea
                         value={descriptionDraft}
                         onChange={(e) => setDescriptionDraft(e.target.value)}
                         className="w-full h-32 bg-[var(--color-bg-main)] border border-[var(--color-border-main)] text-white p-2 font-mono text-xs focus:outline-none focus:border-[var(--color-primary)] resize-none normal-case tracking-normal"
                         placeholder="Use markdown to document this component..."
                       />
                       <div className="flex justify-end gap-2">
                         <button 
                           onClick={() => setIsEditingDescription(false)}
                           className="text-[var(--color-text-muted)] hover:text-white transition-colors"
                         >
                           Cancel
                         </button>
                         <button 
                           onClick={handleSaveDescription}
                           className="bg-[var(--color-primary)] text-[var(--color-bg-main)] px-2 py-0.5 font-bold hover:bg-white transition-colors"
                         >
                           Save
                         </button>
                       </div>
                     </div>
                   ) : (
                     <div className="text-xs text-[var(--color-text-secondary)] normal-case tracking-normal leading-relaxed markdown-body">
                        {nodeData?.description ? (
                          <Markdown>{nodeData.description}</Markdown>
                        ) : (
                          <span className="italic text-[var(--color-text-muted)]">No description provided. Click edit to document this node.</span>
                        )}
                     </div>
                   )}
                </div>

                {nodeData?.rationale && (
                  <div className="pt-2 border-t border-[var(--color-border-subtle)] text-xs text-[var(--color-text-secondary)] normal-case tracking-normal">
                    <span className="block text-white mb-1 uppercase tracking-widest text-[10px]">Rationale:</span>
                    {nodeData.rationale}
                  </div>
                )}
              </div>
            </div>
          );
        })()}
      </div>
    </div>
  );
}
