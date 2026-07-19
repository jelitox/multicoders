"""End-to-end demo: a knowledge-graph agent built on GraphIndexToolkit.

The script ingests a small heterogeneous markdown corpus, embeds and
assembles it into a ``rustworkx`` knowledge graph, runs cross-domain
resolution to discover implicit connections, partitions the graph
into communities via Louvain, and surfaces the whole thing to a
:class:`parrot.bots.agent.BasicAgent` through
:class:`parrot_tools.graphindex.GraphIndexToolkit`.

Mirrors :mod:`examples.pageindex.pageindex_compliance_agent` in shape
and intent — every public capability of GraphIndex is exercised
against the same corpus, then a natural-language question is dispatched
to the agent.

Pipeline (in-memory, no ArangoDB / pgvector required):

1. ``LoaderExtractor`` reads each markdown file under
   ``examples/graphindex/data/`` through a tiny inline loader and
   pipes the body through a :class:`PageIndexToolkit` so each section
   becomes a ``UniversalNode(kind=SECTION)`` with a ``content_ref``
   sidecar.
2. Five seed ``CONCEPT`` nodes are added by hand so the cross-domain
   resolver has a non-document domain to link against.
3. ``GraphIndexEmbedder`` embeds every node's summary into a FAISS
   index using a small sentence-transformer model.
4. ``GraphAssembler`` materialises the ``rustworkx.PyDiGraph``.
5. ``resolve_cross_domain`` infers ``MENTIONS`` edges between concepts
   and sections by cosine similarity.
6. ``detect_communities`` (FEAT-191) partitions the graph with
   Louvain; the membership rides on each node's
   ``domain_tags['community_id']``.
7. ``compute_analytics`` + ``generate_report`` write
   ``examples/graphindex/store/GRAPH_REPORT.md``.
8. A :class:`GraphIndexToolkit` is constructed over the assembled
   state and registered with a :class:`BasicAgent` for a grounded
   natural-language demo.

Requirements:
    * ``GOOGLE_API_KEY`` reachable via navconfig (for the final agent
      Q&A — the indexing pipeline itself is fully offline).
    * ``ai-parrot[embeddings]`` installed so a sentence-transformer
      embedding model is available locally.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

from parrot.bots.agent import BasicAgent
from parrot.clients.google.client import GoogleGenAIClient
from parrot.models.google import GoogleModel
from parrot.knowledge.graphindex import (
    NodeKind,
    SignalRelevanceConfig,
    UniversalNode,
    detect_communities,
)
from parrot.knowledge.graphindex.analytics import (
    compute_analytics,
    generate_report,
)
from parrot.knowledge.graphindex.assemble import GraphAssembler
from parrot.knowledge.graphindex.embed import GraphIndexEmbedder
from parrot.knowledge.graphindex.extractors.loader import LoaderExtractor
from parrot.knowledge.graphindex.resolve import (
    ResolutionConfig,
    resolve_cross_domain,
)
from parrot.pageindex import PageIndexLLMAdapter, PageIndexToolkit
from parrot_tools.graphindex import GraphIndexToolkit


LOG = logging.getLogger("graphindex_corpus_agent")


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
# Heavy model for the final Q&A; light model handles the PageIndex
# per-section summarisation that the loader extractor triggers.

HEAVY_MODEL = GoogleModel.GEMINI_3_FLASH_PREVIEW.value
LIGHT_MODEL = GoogleModel.GEMINI_3_FLASH_LITE_PREVIEW.value

# Embedding model used by GraphIndexEmbedder. Must be available through
# parrot.embeddings.registry — a 384-dim MiniLM is the standard default.
EMBEDDING_MODEL = "all-MiniLM-L6-v2"
EMBEDDING_DIM = 384


# ---------------------------------------------------------------------------
# Storage layout
# ---------------------------------------------------------------------------

DATA_DIR = Path("examples/graphindex/data")
STORE_DIR = Path("examples/graphindex/store")
TENANT_ID = "graphindex-demo"


# ---------------------------------------------------------------------------
# Seed concepts — give the cross-domain resolver something to link against
# ---------------------------------------------------------------------------

SEED_CONCEPTS: list[dict] = [
    {
        "node_id": "concept-logical-access",
        "title": "Logical Access Control",
        "summary": (
            "Restricting who can read, write, or assume which resource. "
            "Covers IAM least privilege, MFA, role assumption, audited "
            "privileged sessions, and access reviews. Maps to SOC 2 CC6.1 "
            "and underpins every production security posture."
        ),
    },
    {
        "node_id": "concept-monitoring",
        "title": "Continuous Security Monitoring",
        "summary": (
            "Continuous detection and reporting of anomalies in cloud "
            "infrastructure. CloudTrail organisation-wide trails, "
            "GuardDuty findings, and Security Hub aggregations feed the "
            "monitoring pipeline. Maps to SOC 2 CC7.2."
        ),
    },
    {
        "node_id": "concept-incident-response",
        "title": "Incident Response Lifecycle",
        "summary": (
            "The lifecycle from detection through containment, "
            "eradication, recovery, and post-mortem. Produces the "
            "evidence trail auditors sample for SOC 2 CC7.3."
        ),
    },
    {
        "node_id": "concept-service-scalability",
        "title": "Independent Service Scalability",
        "summary": (
            "Scaling services independently against their own load "
            "signal — request rate, queue depth, custom metric. "
            "Horizontal pod autoscaling, cluster autoscaling, and "
            "domain-driven service boundaries all serve this goal."
        ),
    },
    {
        "node_id": "concept-container-orchestration",
        "title": "Container Orchestration",
        "summary": (
            "Production patterns for running containerised workloads on "
            "Kubernetes — Deployment/StatefulSet/Job controllers, image "
            "digest pinning, resource requests and limits, rolling "
            "updates with controlled surge."
        ),
    },
]


# ---------------------------------------------------------------------------
# Agent system prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are an architecture-and-compliance assistant grounded in a small
knowledge graph indexed from five markdown documents:

- 01_aws_security.md       — AWS security best practices
- 02_soc2_overview.md      — SOC 2 Trust Services Criteria primer
- 03_incident_response.md  — incident response playbook
- 04_microservices.md      — microservices architecture patterns
- 05_kubernetes.md         — Kubernetes deployment patterns

You can call any of these GraphIndex tools (the full list of 19 is
auto-registered):

- graphindex_find_node(query)               — semantic node lookup
- graphindex_search_hybrid(query, top_k)    — semantic + degree boost
- graphindex_get_neighborhood(node_id, depth)
- graphindex_find_central_nodes(top_k, metric)
- graphindex_list_communities(min_size)     — Louvain partition
- graphindex_find_community(node_id)
- graphindex_neighborhood_by_relevance(node_id, top_k)  — FEAT-190
- graphindex_explain(node_id)

When the user asks a question:
1. Pick the tool that grounds the answer — semantic search for
   "where in the corpus", communities for "which topics cluster
   together", neighborhood for "what relates to X".
2. Cite the document filename and section title for any factual
   claim that came from the graph.
3. If the graph does not contain the answer, say so explicitly
   rather than guessing.
"""


# ---------------------------------------------------------------------------
# Tiny inline hierarchical loader
# ---------------------------------------------------------------------------
# LoaderExtractor._is_hierarchical() looks up the loader class name in
# HIERARCHICAL_LOADERS = {"PDFLoader", "MarkdownLoader", "DOCXLoader",
# "EpubLoader", "DocxLoader", "PdfLoader"}. Defining a class called
# "MarkdownLoader" here lets us reuse the toolkit-routed extraction
# path without pulling in markitdown's transitive dependencies.

class MarkdownLoader:
    """Minimal hierarchical-content loader for the demo.

    Returns one ``Document``-shaped object per file (just enough surface
    area for :class:`LoaderExtractor` to consume).
    """

    async def _load(self, source: str) -> list:
        text = Path(source).read_text(encoding="utf-8")
        return [SimpleNamespace(page_content=text, metadata={"source": source})]


# ---------------------------------------------------------------------------
# Demo helpers
# ---------------------------------------------------------------------------

def _print_header(title: str) -> None:
    bar = "=" * 70
    print(f"\n{bar}\n  {title}\n{bar}")


def _print_node_rows(rows: list[dict], score_key: str = "similarity_score") -> None:
    if not rows:
        print("  (no rows)")
        return
    for r in rows:
        nid = r.get("node_id", "")
        title = (r.get("title") or "")[:55]
        kind = r.get("kind", "")
        score = r.get(score_key)
        score_str = f"  {score_key}={score:.4f}" if isinstance(score, (int, float)) else ""
        print(f"  [{nid}] {title:<55} kind={kind:<10}{score_str}")


# ---------------------------------------------------------------------------
# Pipeline stages — wired explicitly (no GraphIndexBuilder so the demo
# runs without ArangoDB / pgvector)
# ---------------------------------------------------------------------------

async def extract_corpus(
    pageindex_toolkit: PageIndexToolkit,
    data_dir: Path,
) -> tuple[list, list]:
    """Run LoaderExtractor over every markdown file in ``data_dir``."""
    extractor = LoaderExtractor(toolkit=pageindex_toolkit)
    loader = MarkdownLoader()

    all_nodes: list = []
    all_edges: list = []

    md_files = sorted(data_dir.glob("*.md"))
    if not md_files:
        raise FileNotFoundError(
            f"No markdown files found in {data_dir}. "
            f"Expected the example corpus shipped alongside this script."
        )

    for md_path in md_files:
        LOG.info("extracting %s", md_path.name)
        nodes, edges = await extractor.extract(loader, str(md_path))
        LOG.info("  -> %d node(s), %d edge(s)", len(nodes), len(edges))
        all_nodes.extend(nodes)
        all_edges.extend(edges)

    return all_nodes, all_edges


def build_seed_concept_nodes() -> list[UniversalNode]:
    """Materialise the seed concept nodes from ``SEED_CONCEPTS``."""
    return [
        UniversalNode(
            node_id=c["node_id"],
            kind=NodeKind.CONCEPT,
            title=c["title"],
            source_uri=f"seed://{c['node_id']}",
            summary=c["summary"],
        )
        for c in SEED_CONCEPTS
    ]


async def demo_search(toolkit: GraphIndexToolkit) -> None:
    queries = [
        "least-privilege IAM and access reviews",
        "rolling updates with controlled surge",
        "domain events and asynchronous communication",
    ]
    _print_header("search_hybrid — semantic + degree boost")
    for q in queries:
        print(f"\n  query: {q!r}")
        rows = await toolkit.search_hybrid(q, top_k=4)
        _print_node_rows(rows, score_key="combined_score")


async def demo_neighborhood(toolkit: GraphIndexToolkit) -> None:
    _print_header("get_neighborhood — explore around the access-control concept")
    nbhd = await toolkit.get_neighborhood("concept-logical-access", depth=2)
    print(f"  center: {nbhd['center']}")
    print(f"  nodes : {len(nbhd['nodes'])}")
    print(f"  edges : {len(nbhd['edges'])}")
    for node in nbhd["nodes"][:8]:
        print(
            f"    - [{node.get('node_id'):<16}] "
            f"{(node.get('title') or '')[:55]:<55} kind={node.get('kind')}"
        )


async def demo_central_nodes(toolkit: GraphIndexToolkit) -> None:
    _print_header("find_central_nodes — graph backbone")
    rows = await toolkit.find_central_nodes(top_k=8, metric="betweenness")
    _print_node_rows(rows, score_key="centrality_score")


async def demo_relevance(toolkit: GraphIndexToolkit) -> None:
    _print_header(
        "relevance — five-signal score between two concepts (FEAT-190)"
    )
    result = await toolkit.relevance(
        "concept-logical-access", "concept-monitoring",
    )
    print(json.dumps(result, indent=2, default=str))

    _print_header(
        "neighborhood_by_relevance — top-K relevant to incident-response"
    )
    rows = await toolkit.neighborhood_by_relevance(
        "concept-incident-response", top_k=6,
    )
    _print_node_rows(rows, score_key="combined")


async def demo_communities(toolkit: GraphIndexToolkit) -> None:
    _print_header("list_communities — Louvain partition (FEAT-191)")
    communities = await toolkit.list_communities(min_size=2)
    if not communities:
        print("  (no communities of size ≥ 2 — graph may be very small)")
        return
    for c in communities:
        print(
            f"  community {c['community_id']}  size={c['size']:<3}"
            f"  cohesion={c['cohesion']:.3f}"
        )
        for title in c["top_titles"]:
            print(f"      - {title}")


async def demo_agent(toolkit: GraphIndexToolkit) -> None:
    _print_header("BasicAgent.ask — grounded natural-language Q&A")
    tools = toolkit.get_tools()
    LOG.info("registering %d GraphIndex tools with the agent", len(tools))

    agent = BasicAgent(
        name="GraphIndexAgent",
        llm=f"google:{HEAVY_MODEL}",
        system_prompt=SYSTEM_PROMPT,
        tools=list(tools),
        temperature=0.1,
    )
    await agent.configure()

    async with agent:
        prompt = (
            "Looking at this knowledge graph, which two topic clusters "
            "(communities) dominate the corpus, and which document "
            "sections bridge between them? Use list_communities first "
            "and then explain any cross-cluster ties via "
            "search_hybrid or get_neighborhood. Cite the document "
            "filenames in your answer."
        )
        print(f"User: {prompt}\n")
        response = await agent.ask(prompt)
        text = getattr(response, "output", None) or str(response)
        print(f"Agent:\n{text}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def amain(
    data_dir: Path,
    store_dir: Path,
    skip_agent: bool,
    reset: bool,
) -> int:
    if not data_dir.is_dir():
        print(f"ERROR: corpus directory not found at {data_dir}", file=sys.stderr)
        return 2

    store_dir.mkdir(parents=True, exist_ok=True)
    pageindex_storage = store_dir / "pageindex"

    if reset:
        # Drop the per-document PageIndex trees so a rebuild starts clean.
        if pageindex_storage.is_dir():
            for child in sorted(pageindex_storage.iterdir()):
                if child.is_file():
                    child.unlink()
                elif child.is_dir():
                    for grand in child.iterdir():
                        if grand.is_file():
                            grand.unlink()
                    try:
                        child.rmdir()
                    except OSError:
                        pass
            LOG.info("cleared PageIndex storage at %s", pageindex_storage)
        report_file = store_dir / "GRAPH_REPORT.md"
        if report_file.exists():
            report_file.unlink()
            LOG.info("removed cached %s", report_file)

    pageindex_storage.mkdir(parents=True, exist_ok=True)

    # --------------------------------------------------------------
    # PageIndex toolkit — used by LoaderExtractor for sidecar content
    # --------------------------------------------------------------
    async with GoogleGenAIClient() as client:
        adapter = PageIndexLLMAdapter(client=client, model=LIGHT_MODEL)
        pageindex_toolkit = PageIndexToolkit(
            adapter=adapter,
            storage_dir=pageindex_storage,
            lightweight_model=LIGHT_MODEL,
        )

        # ----------------------------------------------------------
        # Stage 1: extract
        # ----------------------------------------------------------
        _print_header("Stage 1 — LoaderExtractor over markdown corpus")
        doc_nodes, doc_edges = await extract_corpus(pageindex_toolkit, data_dir)
        concept_nodes = build_seed_concept_nodes()
        all_nodes = doc_nodes + concept_nodes
        all_edges = list(doc_edges)
        print(
            f"  document/section nodes: {len(doc_nodes)}\n"
            f"  concept seed nodes    : {len(concept_nodes)}\n"
            f"  CONTAINS edges        : {len(doc_edges)}"
        )

        # ----------------------------------------------------------
        # Stage 2: embed
        # ----------------------------------------------------------
        _print_header("Stage 2 — GraphIndexEmbedder (FAISS)")
        embedder = GraphIndexEmbedder(
            model_name=EMBEDDING_MODEL,
            dimension=EMBEDDING_DIM,
        )
        all_nodes = await embedder.embed_nodes(all_nodes)
        embedded = sum(1 for n in all_nodes if n.embedding_ref is not None)
        print(f"  embedded {embedded}/{len(all_nodes)} nodes")
        print(f"  faiss index: {embedder.index.ntotal} vectors @ dim {embedder.dimension}")

        # ----------------------------------------------------------
        # Stage 3: assemble
        # ----------------------------------------------------------
        _print_header("Stage 3 — GraphAssembler (rustworkx PyDiGraph)")
        assembler = GraphAssembler(tenant_id=TENANT_ID)
        assembler.add_nodes(all_nodes)
        assembler.add_edges(all_edges)
        print(
            f"  graph: {assembler.node_count} nodes, "
            f"{assembler.edge_count} edges"
        )

        # ----------------------------------------------------------
        # Stage 4: cross-domain resolution
        # ----------------------------------------------------------
        _print_header("Stage 4 — resolve_cross_domain (inferred MENTIONS edges)")
        inferred_edges = await resolve_cross_domain(
            all_nodes,
            embedder,
            ResolutionConfig(threshold=0.45, max_edges_per_node=6),
        )
        assembler.add_edges(inferred_edges)
        all_edges.extend(inferred_edges)
        print(f"  inferred {len(inferred_edges)} cross-domain edge(s)")
        for e in inferred_edges[:8]:
            print(
                f"    {e.source_id:>16} -> {e.target_id:<16}  "
                f"confidence={e.confidence:.3f}"
            )

        # ----------------------------------------------------------
        # Stage 5: community detection
        # ----------------------------------------------------------
        _print_header("Stage 5 — detect_communities (Louvain, FEAT-191)")
        signal_config = SignalRelevanceConfig()
        communities_result = detect_communities(
            graph=assembler.graph,
            nodes=all_nodes,
            resolution=1.0,
            signal_config=signal_config,
            embedder=embedder,
            write_back_to_nodes=True,
        )
        print(
            f"  partition: {len(communities_result.communities)} "
            f"communities, modularity={communities_result.modularity:.4f}"
        )

        # ----------------------------------------------------------
        # Stage 6: analytics + report
        # ----------------------------------------------------------
        _print_header("Stage 6 — analytics + GRAPH_REPORT.md")
        analytics = compute_analytics(assembler.graph, all_nodes, all_edges)
        analytics.communities = communities_result
        report_path = generate_report(analytics, store_dir)
        print(f"  report: {report_path}")
        print(f"  god_nodes: {len(analytics.god_nodes)}")
        print(f"  surprising_connections: {len(analytics.surprising_connections)}")

        # ----------------------------------------------------------
        # Toolkit — read + write surface for the agent
        # ----------------------------------------------------------
        toolkit = GraphIndexToolkit(
            graph=assembler.graph,
            faiss_index=embedder.index,
            node_map=dict(assembler._node_index_map),
            node_id_list=list(embedder._node_id_map),
            client=client,
            assembler=assembler,
            embedder=embedder,
            nodes=all_nodes,
            signal_config=signal_config,
        )

        await demo_search(toolkit)
        await demo_neighborhood(toolkit)
        await demo_central_nodes(toolkit)
        await demo_relevance(toolkit)
        await demo_communities(toolkit)

        _print_header("Persisted state summary")
        print(json.dumps(
            {
                "tenant_id": TENANT_ID,
                "nodes": len(all_nodes),
                "edges": len(all_edges),
                "inferred_edges": len(inferred_edges),
                "communities": len(communities_result.communities),
                "modularity": round(communities_result.modularity, 4),
                "report": str(report_path),
                "pageindex_storage": str(pageindex_storage),
            },
            indent=2,
        ))

        if not skip_agent:
            await demo_agent(toolkit)

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="GraphIndex corpus-agent demo."
    )
    parser.add_argument(
        "--data-dir",
        default=str(DATA_DIR),
        help=f"Directory of markdown files to ingest (default: {DATA_DIR}).",
    )
    parser.add_argument(
        "--store-dir",
        default=str(STORE_DIR),
        help=f"Where to write GRAPH_REPORT.md and PageIndex sidecars (default: {STORE_DIR}).",
    )
    parser.add_argument(
        "--skip-agent",
        action="store_true",
        help="Skip the final BasicAgent question (only run pipeline demos).",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Wipe cached PageIndex trees and GRAPH_REPORT.md before rebuilding.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(levelname)s %(name)s: %(message)s",
    )

    sys.exit(asyncio.run(amain(
        Path(args.data_dir),
        Path(args.store_dir),
        args.skip_agent,
        args.reset,
    )))


if __name__ == "__main__":
    main()
