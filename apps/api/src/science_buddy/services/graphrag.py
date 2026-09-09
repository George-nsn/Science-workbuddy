import hashlib
import re
from collections import Counter, defaultdict, deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

import networkx as nx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from science_buddy.infrastructure.models import (
    Chunk,
    GraphCommunity,
    GraphCommunityMember,
    GraphCommunityReport,
    GraphEdge,
    GraphEntityMention,
    GraphNode,
    GraphPathAudit,
)

_GRAPH_INDEX_VERSION = "deterministic-graphrag-v1"
_COMMUNITY_ALGORITHM = "networkx-louvain-fixed-seed"
_MAX_ENTITY_MENTIONS = 50000
_TERM_PATTERN = re.compile(r"[A-Za-z0-9_./+-]{2,}|[\u4e00-\u9fff]+")


@dataclass(frozen=True, slots=True)
class GraphRagIndexResult:
    communities: int
    reports: int
    mentions: int


@dataclass(frozen=True, slots=True)
class GraphRagHit:
    chunk_id: UUID
    paper_id: UUID
    score: float
    provenance: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class GraphRagSearchResult:
    mode: str
    hits: tuple[GraphRagHit, ...]
    metrics: Mapping[str, str | int | float]
    paths: tuple[Mapping[str, object], ...] = ()


class FullGraphRagIndexer:
    """Build deterministic mentions, hierarchical communities, and grounded reports."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def rebuild(
        self,
        *,
        project_id: UUID,
        scope_key: str,
    ) -> GraphRagIndexResult:
        await self._session.execute(
            delete(GraphPathAudit).where(
                GraphPathAudit.project_id == project_id,
                GraphPathAudit.scope_key == scope_key,
            )
        )
        await self._session.execute(
            delete(GraphEntityMention).where(
                GraphEntityMention.project_id == project_id,
                GraphEntityMention.scope_key == scope_key,
            )
        )
        community_ids = select(GraphCommunity.id).where(
            GraphCommunity.project_id == project_id,
            GraphCommunity.scope_key == scope_key,
        )
        await self._session.execute(
            delete(GraphCommunityReport).where(
                GraphCommunityReport.community_id.in_(community_ids)
            )
        )
        await self._session.execute(
            delete(GraphCommunityMember).where(
                GraphCommunityMember.community_id.in_(community_ids)
            )
        )
        await self._session.execute(
            delete(GraphCommunity).where(
                GraphCommunity.project_id == project_id,
                GraphCommunity.scope_key == scope_key,
            )
        )
        await self._session.flush()
        nodes = list(
            (
                await self._session.scalars(
                    select(GraphNode).where(
                        GraphNode.project_id == project_id,
                        GraphNode.scope_key == scope_key,
                    )
                )
            ).all()
        )
        edges = list(
            (
                await self._session.scalars(
                    select(GraphEdge).where(
                        GraphEdge.project_id == project_id,
                        GraphEdge.scope_key == scope_key,
                    )
                )
            ).all()
        )
        mentions = await self._build_mentions(project_id, scope_key, nodes)
        if mentions:
            await self._session.flush()
            edges = list(
                (
                    await self._session.scalars(
                        select(GraphEdge).where(
                            GraphEdge.project_id == project_id,
                            GraphEdge.scope_key == scope_key,
                        )
                    )
                ).all()
            )
        communities, reports = await self._build_communities(
            project_id,
            scope_key,
            nodes,
            edges,
        )
        await self._session.flush()
        return GraphRagIndexResult(communities, reports, mentions)

    async def _build_mentions(
        self,
        project_id: UUID,
        scope_key: str,
        nodes: Sequence[GraphNode],
    ) -> int:
        chunk_nodes = [node for node in nodes if node.node_type == "chunk"]
        chunk_ids = [
            UUID(node.node_key.removeprefix("chunk:"))
            for node in chunk_nodes
            if node.node_key.startswith("chunk:")
        ]
        chunks = {
            chunk.id: chunk
            for chunk in (
                await self._session.scalars(select(Chunk).where(Chunk.id.in_(chunk_ids)))
            ).all()
        }
        searchable_nodes = [
            node
            for node in nodes
            if node.node_type in {"mesh", "author", "journal", "tag"}
            and len(node.label.strip()) >= 3
        ]
        count = 0
        for chunk in chunks.values():
            folded = chunk.text.casefold()
            for node in searchable_nodes:
                needle = node.label.strip().casefold()
                start = folded.find(needle)
                occurrences = 0
                while start >= 0 and occurrences < 3 and count < _MAX_ENTITY_MENTIONS:
                    end = start + len(needle)
                    self._session.add(
                        GraphEntityMention(
                            project_id=project_id,
                            scope_key=scope_key,
                            node_key=node.node_key,
                            chunk_id=chunk.id,
                            mention_text=chunk.text[start:end],
                            char_start=start,
                            char_end=end,
                            extractor="deterministic-label-match-v1",
                            confidence=1.0,
                        )
                    )
                    evidence_key = hashlib.sha256(
                        f"{node.node_key}:{chunk.id}:{start}:{end}".encode()
                    ).hexdigest()[:32]
                    self._session.add(
                        GraphEdge(
                            project_id=project_id,
                            scope_key=scope_key,
                            source_key=f"chunk:{chunk.id}",
                            relation="MENTIONS",
                            target_key=node.node_key,
                            evidence_chunk_id=chunk.id,
                            evidence_key=evidence_key,
                            provenance="deterministic_label_match",
                            confidence=1.0,
                        )
                    )
                    count += 1
                    occurrences += 1
                    start = folded.find(needle, end)
        return count

    async def _build_communities(
        self,
        project_id: UUID,
        scope_key: str,
        nodes: Sequence[GraphNode],
        edges: Sequence[GraphEdge],
    ) -> tuple[int, int]:
        nodes_by_key = {node.node_key: node for node in nodes}
        entity_keys = sorted(
            node.node_key for node in nodes if node.node_type != "chunk"
        )
        graph: Any = nx.Graph()
        graph.add_nodes_from(entity_keys)
        for edge in sorted(edges, key=lambda item: str(item.id)):
            if edge.relation == "HAS_CHUNK":
                continue
            if edge.source_key not in graph or edge.target_key not in graph:
                continue
            weight = max(float(edge.confidence), 0.01)
            if graph.has_edge(edge.source_key, edge.target_key):
                graph[edge.source_key][edge.target_key]["weight"] += weight
            else:
                graph.add_edge(edge.source_key, edge.target_key, weight=weight)
        fine_groups = self._louvain_groups(graph)
        coarse_groups = self._coarse_groups(graph, fine_groups)
        fine_to_parent: dict[int, GraphCommunity] = {}
        all_communities: list[tuple[GraphCommunity, list[str]]] = []
        for coarse_index, fine_indexes in enumerate(coarse_groups):
            members = sorted(
                {
                    node_key
                    for fine_index in fine_indexes
                    for node_key in fine_groups[fine_index]
                }
            )
            community = self._community(
                project_id,
                scope_key,
                level=1,
                members=members,
                nodes_by_key=nodes_by_key,
                key_prefix=f"coarse-{coarse_index}",
                parent=None,
            )
            self._session.add(community)
            await self._session.flush()
            all_communities.append((community, members))
            for fine_index in fine_indexes:
                fine_to_parent[fine_index] = community
        for fine_index, members_value in enumerate(fine_groups):
            members = sorted(members_value)
            community = self._community(
                project_id,
                scope_key,
                level=0,
                members=members,
                nodes_by_key=nodes_by_key,
                key_prefix=f"fine-{fine_index}",
                parent=fine_to_parent.get(fine_index),
            )
            self._session.add(community)
            await self._session.flush()
            all_communities.append((community, members))
        edge_lookup = self._edge_lookup(edges)
        report_count = 0
        for community, members in all_communities:
            subgraph = graph.subgraph(members)
            weighted_degrees = dict(subgraph.degree(weight="weight"))
            max_degree = max((float(value) for value in weighted_degrees.values()), default=1.0)
            for node_key in members:
                centrality = float(weighted_degrees.get(node_key, 0.0)) / max(max_degree, 1.0)
                self._session.add(
                    GraphCommunityMember(
                        community_id=community.id,
                        node_key=node_key,
                        role="hub" if centrality >= 0.75 else "member",
                        centrality=centrality,
                    )
                )
            report = self._report(
                community,
                members,
                nodes_by_key,
                edges,
                edge_lookup,
                weighted_degrees,
            )
            self._session.add(report)
            report_count += 1
        return len(all_communities), report_count

    @staticmethod
    def _louvain_groups(graph: Any) -> list[set[str]]:
        if graph.number_of_nodes() == 0:
            return []
        if graph.number_of_edges() == 0:
            return [{str(node)} for node in sorted(graph.nodes())]
        values = nx.community.louvain_communities(
            graph,
            weight="weight",
            resolution=1.2,
            seed=0,
        )
        return sorted(
            ({str(node) for node in value} for value in values),
            key=lambda value: min(value),
        )

    @staticmethod
    def _coarse_groups(graph: Any, fine_groups: Sequence[set[str]]) -> list[list[int]]:
        if not fine_groups:
            return []
        node_to_fine = {
            node_key: index
            for index, members in enumerate(fine_groups)
            for node_key in members
        }
        coarse: Any = nx.Graph()
        coarse.add_nodes_from(range(len(fine_groups)))
        for source, target, data in graph.edges(data=True):
            left = node_to_fine[str(source)]
            right = node_to_fine[str(target)]
            if left == right:
                continue
            weight = float(data.get("weight", 1.0))
            if coarse.has_edge(left, right):
                coarse[left][right]["weight"] += weight
            else:
                coarse.add_edge(left, right, weight=weight)
        return [
            sorted(int(value) for value in component)
            for component in sorted(
                nx.connected_components(coarse),
                key=lambda value: min(value),
            )
        ]

    @staticmethod
    def _community(
        project_id: UUID,
        scope_key: str,
        *,
        level: int,
        members: Sequence[str],
        nodes_by_key: Mapping[str, GraphNode],
        key_prefix: str,
        parent: GraphCommunity | None,
    ) -> GraphCommunity:
        digest = hashlib.sha256("\n".join(members).encode()).hexdigest()[:20]
        community_key = f"{key_prefix}-{digest}"
        labels = [nodes_by_key[key].label for key in members if key in nodes_by_key]
        title = " / ".join(labels[:3]) or "Untitled graph community"
        return GraphCommunity(
            id=uuid5(
                NAMESPACE_URL,
                f"science-buddy:{project_id}:{scope_key}:{level}:{community_key}",
            ),
            project_id=project_id,
            scope_key=scope_key,
            community_key=community_key,
            level=level,
            parent_community_id=parent.id if parent else None,
            title=title[:300],
            algorithm=_COMMUNITY_ALGORITHM,
            algorithm_version=_GRAPH_INDEX_VERSION,
            member_count=len(members),
            stats={},
        )

    def _report(
        self,
        community: GraphCommunity,
        members: Sequence[str],
        nodes_by_key: Mapping[str, GraphNode],
        edges: Sequence[GraphEdge],
        edge_lookup: Mapping[str, list[GraphEdge]],
        weighted_degrees: Mapping[str, float],
    ) -> GraphCommunityReport:
        ranked_keys = sorted(
            members,
            key=lambda key: (-float(weighted_degrees.get(key, 0.0)), key),
        )
        top_nodes = [nodes_by_key[key] for key in ranked_keys[:10] if key in nodes_by_key]
        member_set = set(members)
        internal_edges = [
            edge
            for edge in edges
            if edge.source_key in member_set and edge.target_key in member_set
        ]
        relation_counts = Counter(edge.relation for edge in internal_edges)
        provenance_counts = Counter(edge.provenance for edge in internal_edges)
        chunk_ids = self._representative_chunks(ranked_keys, edge_lookup, limit=12)
        paper_ids = sorted(
            {
                key.removeprefix("paper:")
                for key in members
                if key.startswith("paper:")
            }
        )
        years = sorted(
            int(node.properties["publication_year"])
            for node in top_nodes
            if isinstance(node.properties.get("publication_year"), int)
        )
        entity_text = "、".join(node.label for node in top_nodes[:6]) or "无中心实体"
        relation_text = "、".join(
            f"{relation}:{count}"
            for relation, count in sorted(relation_counts.items())[:8]
        ) or "无内部关系"
        period = (
            f"{years[0]}–{years[-1]}" if years else "出版时间未知"
        )
        summary = (
            f"该社区包含 {len(members)} 个实体，中心实体为 {entity_text}；"
            f"主要关系为 {relation_text}；时间范围 {period}。"
        )
        report_text = (
            f"# {community.title}\n\n{summary}\n\n"
            f"## 中心实体\n{entity_text}\n\n"
            f"## 关系分布\n{relation_text}\n\n"
            f"## Provenance\n"
            + "、".join(
                f"{source}:{count}"
                for source, count in sorted(provenance_counts.items())
            )
        )
        community.stats = {
            "entity_types": dict(Counter(node.node_type for node in top_nodes)),
            "relation_counts": dict(relation_counts),
            "provenance_counts": dict(provenance_counts),
            "publication_year_min": years[0] if years else None,
            "publication_year_max": years[-1] if years else None,
            "representative_chunk_count": len(chunk_ids),
        }
        return GraphCommunityReport(
            id=uuid5(
                NAMESPACE_URL,
                f"science-buddy:graph-report:{community.id}:{_GRAPH_INDEX_VERSION}",
            ),
            community_id=community.id,
            report_version=_GRAPH_INDEX_VERSION,
            title=community.title,
            summary=summary,
            report_text=report_text,
            evidence_bundle={
                "node_keys": list(ranked_keys[:20]),
                "edge_ids": [str(edge.id) for edge in internal_edges[:50]],
                "chunk_ids": [str(value) for value in chunk_ids],
                "paper_ids": paper_ids[:30],
                "provenance": dict(provenance_counts),
                "period": {
                    "min": years[0] if years else None,
                    "max": years[-1] if years else None,
                },
            },
            generated_by="deterministic-community-template-v1",
        )

    @staticmethod
    def _edge_lookup(edges: Sequence[GraphEdge]) -> dict[str, list[GraphEdge]]:
        output: dict[str, list[GraphEdge]] = defaultdict(list)
        for edge in edges:
            output[edge.source_key].append(edge)
            output[edge.target_key].append(edge)
        return output

    @staticmethod
    def _representative_chunks(
        node_keys: Sequence[str],
        edge_lookup: Mapping[str, list[GraphEdge]],
        *,
        limit: int,
    ) -> list[UUID]:
        output: list[UUID] = []
        visited = set(node_keys)
        frontier = deque((node_key, 0) for node_key in node_keys)
        while frontier and len(output) < limit:
            node_key, distance = frontier.popleft()
            if node_key.startswith("chunk:"):
                try:
                    chunk_id = UUID(node_key.removeprefix("chunk:"))
                except ValueError:
                    continue
                if chunk_id not in output:
                    output.append(chunk_id)
                continue
            if distance >= 2:
                continue
            for edge in edge_lookup.get(node_key, []):
                other = edge.target_key if edge.source_key == node_key else edge.source_key
                if other in visited:
                    continue
                visited.add(other)
                frontier.append((other, distance + 1))
        return output


class FullGraphRagRetriever:
    """Deterministic local/global/DRIFT/path GraphRAG retrieval with source audit."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def search(
        self,
        *,
        mode: str,
        query: str,
        project_id: UUID,
        scope_key: str,
        seed_paper_ids: set[UUID] | None = None,
        limit: int = 10,
        hops: int = 2,
        persist_paths: bool = False,
    ) -> GraphRagSearchResult:
        nodes = list(
            (
                await self._session.scalars(
                    select(GraphNode).where(
                        GraphNode.project_id == project_id,
                        GraphNode.scope_key == scope_key,
                    )
                )
            ).all()
        )
        edges = list(
            (
                await self._session.scalars(
                    select(GraphEdge).where(
                        GraphEdge.project_id == project_id,
                        GraphEdge.scope_key == scope_key,
                    )
                )
            ).all()
        )
        if mode == "global":
            return await self._global(query, project_id, scope_key, nodes, limit)
        if mode == "drift":
            return await self._drift(query, project_id, scope_key, nodes, edges, limit, hops)
        if mode == "path":
            return await self._path(
                query,
                project_id,
                scope_key,
                nodes,
                edges,
                seed_paper_ids or set(),
                limit,
                hops,
                persist_paths,
            )
        return self._local(
            query,
            nodes,
            edges,
            seed_paper_ids or set(),
            limit,
            hops,
        )

    def _local(
        self,
        query: str,
        nodes: Sequence[GraphNode],
        edges: Sequence[GraphEdge],
        seed_paper_ids: set[UUID],
        limit: int,
        hops: int,
        extra_seed_keys: Sequence[str] = (),
    ) -> GraphRagSearchResult:
        nodes_by_key = {node.node_key: node for node in nodes}
        seeds = [f"paper:{value}" for value in sorted(seed_paper_ids, key=str)]
        seeds.extend(key for key, _ in self._matching_nodes(query, nodes, limit=8))
        seeds.extend(extra_seed_keys)
        scores: dict[str, float] = {}
        paths: dict[str, list[str]] = {}
        adjacency = self._adjacency(edges)
        frontier = deque((key, 0, [key], 1.0) for key in dict.fromkeys(seeds))
        max_entities = max(200, min(limit * 50, 1000))
        while frontier and len(scores) < max_entities:
            node_key, distance, path, confidence = frontier.popleft()
            if node_key not in nodes_by_key or distance > hops:
                continue
            score = confidence / (distance + 1)
            if score <= scores.get(node_key, -1.0):
                continue
            scores[node_key] = score
            paths[node_key] = path
            if distance == hops:
                continue
            for edge, other in adjacency.get(node_key, []):
                if other in path:
                    continue
                frontier.append(
                    (other, distance + 1, [*path, other], confidence * edge.confidence)
                )
        hits = self._hits_from_nodes(
            scores,
            nodes_by_key,
            edges,
            limit=limit,
            provenance_factory=lambda key: {
                "mode": "local",
                "seed_node_keys": seeds[:10],
                "path_nodes": paths.get(key, []),
                "hops": max(len(paths.get(key, [])) - 1, 0),
            },
        )
        return GraphRagSearchResult(
            "local",
            tuple(hits),
            {
                "backend": "full-graphrag-v1",
                "entities_hit": len(scores),
                "edges_scanned": len(edges),
                "communities_hit": 0,
                "paths_found": len(paths),
            },
        )

    async def _global(
        self,
        query: str,
        project_id: UUID,
        scope_key: str,
        nodes: Sequence[GraphNode],
        limit: int,
    ) -> GraphRagSearchResult:
        rows = (
            await self._session.execute(
                select(GraphCommunity, GraphCommunityReport)
                .join(
                    GraphCommunityReport,
                    GraphCommunityReport.community_id == GraphCommunity.id,
                )
                .where(
                    GraphCommunity.project_id == project_id,
                    GraphCommunity.scope_key == scope_key,
                )
            )
        ).all()
        terms = self._terms(query)
        ranked: list[tuple[float, GraphCommunity, GraphCommunityReport]] = []
        for community, report in rows:
            searchable = self._terms(f"{report.title} {report.summary} {report.report_text}")
            overlap = len(terms & searchable) / max(len(terms), 1)
            if overlap > 0:
                ranked.append((overlap, community, report))
        ranked.sort(key=lambda item: (item[0], item[1].level), reverse=True)
        nodes_by_key = {node.node_key: node for node in nodes}
        hits: list[GraphRagHit] = []
        for score, community, report in ranked[: max(limit, 1)]:
            bundle = report.evidence_bundle
            node_keys = [str(value) for value in bundle.get("node_keys", [])]
            chunk_ids = [
                UUID(str(value))
                for value in bundle.get("chunk_ids", [])
                if self._is_uuid(value)
            ]
            hits.extend(
                self._hits_for_chunk_ids(
                    chunk_ids,
                    score=score,
                    nodes_by_key=nodes_by_key,
                    provenance={
                        "mode": "global",
                        "community_id": str(community.id),
                        "community_level": community.level,
                        "report_id": str(report.id),
                        "report_title": report.title,
                        "node_keys": node_keys[:10],
                        "evidence_bundle": bundle,
                    },
                )
            )
        return GraphRagSearchResult(
            "global",
            tuple(self._deduplicate_hits(hits, limit)),
            {
                "backend": "full-graphrag-v1",
                "entities_hit": 0,
                "edges_scanned": 0,
                "communities_hit": len(ranked),
                "paths_found": 0,
            },
        )

    async def _drift(
        self,
        query: str,
        project_id: UUID,
        scope_key: str,
        nodes: Sequence[GraphNode],
        edges: Sequence[GraphEdge],
        limit: int,
        hops: int,
    ) -> GraphRagSearchResult:
        matched = self._matching_nodes(query, nodes, limit=8)
        seed_keys = [key for key, _ in matched]
        community_rows = (
            await self._session.execute(
                select(GraphCommunity, GraphCommunityMember, GraphCommunityReport)
                .join(
                    GraphCommunityMember,
                    GraphCommunityMember.community_id == GraphCommunity.id,
                )
                .join(
                    GraphCommunityReport,
                    GraphCommunityReport.community_id == GraphCommunity.id,
                )
                .where(
                    GraphCommunity.project_id == project_id,
                    GraphCommunity.scope_key == scope_key,
                    GraphCommunityMember.node_key.in_(seed_keys),
                )
                .order_by(GraphCommunity.level, GraphCommunityMember.centrality.desc())
            )
        ).all()
        expanded_seeds = set(seed_keys)
        community_ids: list[str] = []
        reports: list[str] = []
        for community, _member, report in community_rows[:8]:
            community_ids.append(str(community.id))
            reports.append(str(report.id))
            expanded_seeds.update(
                str(value) for value in report.evidence_bundle.get("node_keys", [])[:8]
            )
        local = self._local(
            query,
            nodes,
            edges,
            set(),
            limit * 2,
            hops,
            extra_seed_keys=sorted(expanded_seeds),
        )
        hits = [
            GraphRagHit(
                hit.chunk_id,
                hit.paper_id,
                hit.score,
                {
                    **hit.provenance,
                    "mode": "drift",
                    "drift_expansion": "dynamic-reasoning-community-traversal",
                    "seed_node_keys": seed_keys,
                    "expanded_node_keys": sorted(expanded_seeds)[:30],
                    "community_ids": community_ids,
                    "report_ids": reports,
                },
            )
            for hit in local.hits
        ]
        return GraphRagSearchResult(
            "drift",
            tuple(self._deduplicate_hits(hits, limit)),
            {
                "backend": "full-graphrag-v1",
                "entities_hit": len(expanded_seeds),
                "edges_scanned": len(edges),
                "communities_hit": len(set(community_ids)),
                "paths_found": int(local.metrics.get("paths_found", 0)),
            },
        )

    async def _path(
        self,
        query: str,
        project_id: UUID,
        scope_key: str,
        nodes: Sequence[GraphNode],
        edges: Sequence[GraphEdge],
        seed_paper_ids: set[UUID],
        limit: int,
        hops: int,
        persist_paths: bool,
    ) -> GraphRagSearchResult:
        nodes_by_key = {node.node_key: node for node in nodes}
        matched = [key for key, _ in self._matching_nodes(query, nodes, limit=6)]
        matched.extend(f"paper:{value}" for value in sorted(seed_paper_ids, key=str))
        seeds = [key for key in dict.fromkeys(matched) if key in nodes_by_key][:6]
        graph: Any = nx.Graph()
        edge_by_pair: dict[frozenset[str], GraphEdge] = {}
        for edge in edges:
            if edge.source_key not in nodes_by_key or edge.target_key not in nodes_by_key:
                continue
            cost = 1.0 / max(edge.confidence, 0.01)
            graph.add_edge(edge.source_key, edge.target_key, weight=cost)
            edge_by_pair[frozenset((edge.source_key, edge.target_key))] = edge
        path_values: list[dict[str, object]] = []
        chunk_scores: dict[UUID, tuple[UUID, float, dict[str, object]]] = {}
        for index, source in enumerate(seeds):
            for target in seeds[index + 1 :]:
                try:
                    path = nx.shortest_path(graph, source, target, weight="weight")
                except (nx.NetworkXNoPath, nx.NodeNotFound):
                    continue
                if len(path) - 1 > hops:
                    continue
                path_edges = [
                    edge_by_pair[frozenset((left, right))]
                    for left, right in zip(path, path[1:], strict=False)
                ]
                score = sum(edge.confidence for edge in path_edges) / max(len(path_edges), 1)
                audit: dict[str, object] = {
                    "source_node_key": source,
                    "target_node_key": target,
                    "path_nodes": path,
                    "path_edges": [
                        {
                            "edge_id": str(edge.id),
                            "relation": edge.relation,
                            "provenance": edge.provenance,
                            "confidence": edge.confidence,
                            "evidence_chunk_id": (
                                str(edge.evidence_chunk_id)
                                if edge.evidence_chunk_id
                                else None
                            ),
                        }
                        for edge in path_edges
                    ],
                    "path_score": score,
                }
                path_values.append(audit)
                if persist_paths:
                    self._session.add(
                        GraphPathAudit(
                            project_id=project_id,
                            scope_key=scope_key,
                            query_hash=hashlib.sha256(query.encode()).hexdigest(),
                            source_node_key=source,
                            target_node_key=target,
                            path_nodes=path,
                            path_edges=audit["path_edges"],
                            path_score=score,
                            provenance_bundle={
                                "query": query,
                                "index_version": _GRAPH_INDEX_VERSION,
                            },
                        )
                    )
                def path_provenance(
                    _node_key: str,
                    value: Mapping[str, object] = audit,
                ) -> Mapping[str, object]:
                    return {"mode": "path", **value}

                for hit in self._hits_from_nodes(
                    {key: score for key in path},
                    nodes_by_key,
                    edges,
                    limit=limit,
                    provenance_factory=path_provenance,
                ):
                    existing = chunk_scores.get(hit.chunk_id)
                    if existing is None or hit.score > existing[1]:
                        chunk_scores[hit.chunk_id] = (
                            hit.paper_id,
                            hit.score,
                            dict(hit.provenance),
                        )
                if len(path_values) >= max(limit, 1):
                    break
            if len(path_values) >= max(limit, 1):
                break
        if persist_paths and path_values:
            await self._session.commit()
        hits = [
            GraphRagHit(chunk_id, paper_id, score, provenance)
            for chunk_id, (paper_id, score, provenance) in chunk_scores.items()
        ]
        hits.sort(key=lambda value: value.score, reverse=True)
        return GraphRagSearchResult(
            "path",
            tuple(hits[:limit]),
            {
                "backend": "full-graphrag-v1",
                "entities_hit": len(seeds),
                "edges_scanned": len(edges),
                "communities_hit": 0,
                "paths_found": len(path_values),
            },
            tuple(path_values),
        )

    @classmethod
    def _matching_nodes(
        cls,
        query: str,
        nodes: Sequence[GraphNode],
        *,
        limit: int,
    ) -> list[tuple[str, float]]:
        query_terms = cls._terms(query)
        values: list[tuple[str, float]] = []
        for node in nodes:
            if node.node_type == "chunk":
                continue
            node_terms = cls._terms(node.label)
            overlap = len(query_terms & node_terms) / max(len(query_terms), 1)
            if overlap > 0:
                values.append((node.node_key, overlap))
        return sorted(values, key=lambda item: (item[1], item[0]), reverse=True)[:limit]

    @staticmethod
    def _adjacency(edges: Sequence[GraphEdge]) -> dict[str, list[tuple[GraphEdge, str]]]:
        output: dict[str, list[tuple[GraphEdge, str]]] = defaultdict(list)
        for edge in edges:
            output[edge.source_key].append((edge, edge.target_key))
            output[edge.target_key].append((edge, edge.source_key))
        return output

    def _hits_from_nodes(
        self,
        scores: Mapping[str, float],
        nodes_by_key: Mapping[str, GraphNode],
        edges: Sequence[GraphEdge],
        *,
        limit: int,
        provenance_factory: Callable[[str], Mapping[str, object]],
    ) -> list[GraphRagHit]:
        edge_lookup = FullGraphRagIndexer._edge_lookup(edges)
        hits: list[GraphRagHit] = []
        for node_key, score in sorted(scores.items(), key=lambda item: item[1], reverse=True):
            chunk_ids = FullGraphRagIndexer._representative_chunks(
                [node_key], edge_lookup, limit=3
            )
            hits.extend(
                self._hits_for_chunk_ids(
                    chunk_ids,
                    score=score,
                    nodes_by_key=nodes_by_key,
                    provenance=provenance_factory(node_key),
                )
            )
            if len(hits) >= limit * 3:
                break
        return self._deduplicate_hits(hits, limit)

    @staticmethod
    def _hits_for_chunk_ids(
        chunk_ids: Sequence[UUID],
        *,
        score: float,
        nodes_by_key: Mapping[str, GraphNode],
        provenance: Mapping[str, object],
    ) -> list[GraphRagHit]:
        output: list[GraphRagHit] = []
        for chunk_id in chunk_ids:
            node = nodes_by_key.get(f"chunk:{chunk_id}")
            if node is None:
                continue
            paper_value = node.properties.get("paper_id")
            if not paper_value or not FullGraphRagRetriever._is_uuid(paper_value):
                continue
            output.append(
                GraphRagHit(
                    chunk_id=chunk_id,
                    paper_id=UUID(str(paper_value)),
                    score=score,
                    provenance=provenance,
                )
            )
        return output

    @staticmethod
    def _deduplicate_hits(hits: Sequence[GraphRagHit], limit: int) -> list[GraphRagHit]:
        output: list[GraphRagHit] = []
        seen: set[UUID] = set()
        for hit in sorted(hits, key=lambda value: value.score, reverse=True):
            if hit.chunk_id in seen:
                continue
            seen.add(hit.chunk_id)
            output.append(hit)
            if len(output) >= limit:
                break
        return output

    @staticmethod
    def _terms(value: str) -> set[str]:
        output: set[str] = set()
        for token in _TERM_PATTERN.findall(value.casefold()):
            if re.fullmatch(r"[\u4e00-\u9fff]+", token):
                output.update(token[index : index + 2] for index in range(len(token) - 1))
            else:
                output.add(token)
        return output

    @staticmethod
    def _is_uuid(value: object) -> bool:
        try:
            UUID(str(value))
        except ValueError:
            return False
        return True
