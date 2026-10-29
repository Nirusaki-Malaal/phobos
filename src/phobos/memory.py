"""Project graph memory built locally with Graphify from inspectable SQLite records."""

import json
import os
import time
from importlib.metadata import version

from phobos.config import Settings
from phobos.skills import words
from phobos.store import Store
from phobos.workspace import atomic_text, confined, project_path


class GraphMemory:
    def __init__(self, settings: Settings, store: Store):
        self.settings, self.store = settings, store

    def build(self, project: str):
        from graphify.build import build_from_json
        from networkx.readwrite import json_graph

        project_path(self.settings.workspace_dir, project)
        nodes, edges = [], []

        def node(identifier, label, content, origin, source):
            nodes.append(
                {
                    "id": identifier,
                    "label": label,
                    "type": "concept",
                    "source_file": "",
                    "description": content[:5000],
                    "origin": origin,
                    "source": source,
                    "trust": "data",
                }
            )

        def edge(source, target, relation, confidence="EXTRACTED"):
            edges.append(
                {
                    "source": source,
                    "target": target,
                    "relation": relation,
                    "confidence": confidence,
                    "source_file": "",
                }
            )

        with self.store.connection() as db:
            # One read snapshot prevents mixed ticket/feedback versions across queries.
            db.execute("BEGIN")
            tickets = db.execute(
                "SELECT * FROM tickets WHERE project=? AND status IN ('completed','failed') "
                "ORDER BY id DESC LIMIT 200",
                (project,),
            ).fetchall()
            ticket_ids = {row["id"] for row in tickets}
            for row in tickets:
                node(
                    f"ticket-{row['id']}",
                    row["title"],
                    f"Task: {row['prompt'][:1000]}\nOutcome: {row['status']}\n"
                    + (row["result"] or row["error"])[:3500],
                    "agent",
                    f"/api/tickets/{row['id']}",
                )
            for row in db.execute(
                "SELECT * FROM memories WHERE project=? ORDER BY id DESC LIMIT 200", (project,)
            ):
                node(
                    f"memory-{row['id']}",
                    "Preference " + str(row["id"]),
                    row["content"],
                    "owner",
                    f"/api/memories/{project}",
                )
            for row in db.execute(
                "SELECT * FROM procedures WHERE project=? ORDER BY id DESC LIMIT 100", (project,)
            ):
                if row["status"] != "active":
                    continue
                identifier = f"procedure-{row['id']}"
                node(
                    identifier,
                    f"{row['name']} v{row['version']}",
                    row["instructions"],
                    "owner-reviewed",
                    f"/api/procedures/{project}/{row['id']}",
                )
                if row["source_ticket"] in ticket_ids:
                    edge(identifier, f"ticket-{row['source_ticket']}", "learned_from")
            for row in db.execute(
                "SELECT f.* FROM feedback f JOIN tickets t ON t.id=f.ticket_id "
                "WHERE t.project=? ORDER BY f.id DESC LIMIT 500",
                (project,),
            ):
                if row["ticket_id"] not in ticket_ids:
                    continue
                identifier = f"feedback-{row['id']}"
                node(
                    identifier,
                    "Owner feedback",
                    f"Rating: {row['rating']}\n{row['comment']}",
                    "owner",
                    f"/api/tickets/{row['ticket_id']}/events",
                )
                edge(identifier, f"ticket-{row['ticket_id']}", "evaluates")
            skill_nodes = set()
            for row in db.execute(
                "SELECT u.* FROM skill_usage u JOIN tickets t ON t.id=u.ticket_id "
                "WHERE t.project=? ORDER BY u.created DESC LIMIT 500",
                (project,),
            ):
                if row["ticket_id"] not in ticket_ids:
                    continue
                identifier = f"skill-{row['name']}-{row['digest'][:12]}"
                if identifier not in skill_nodes:
                    node(
                        identifier,
                        row["name"],
                        "Skill digest: " + row["digest"],
                        row["origin"],
                        f"/api/skills/{row['name']}",
                    )
                    skill_nodes.add(identifier)
                edge(f"ticket-{row['ticket_id']}", identifier, "used_skill")
        code_path = self.settings.data_dir / "graphs" / project / "code.json"
        if code_path.is_file():
            code = json.loads(code_path.read_text())
            nodes.extend(code.get("nodes", []))
            edges.extend(code.get("links", code.get("edges", [])))
        # Link lexical overlaps as inferred associations, never as verified facts.
        tokens = {
            row["id"]: words(row.get("label", "") + " " + row.get("description", ""))
            - {"the", "and", "for", "with", "from", "this", "that", "task", "outcome"}
            for row in nodes
        }
        for row in nodes[:300]:
            candidates = sorted(
                (
                    (len(tokens[row["id"]] & tokens[other["id"]]), other["id"])
                    for other in nodes
                    if other["id"] != row["id"]
                ),
                reverse=True,
            )
            for overlap, target in candidates[:3]:
                if overlap >= 3:
                    edge(row["id"], target, "related_terms", "INFERRED")
        graph = build_from_json({"nodes": nodes, "edges": edges}, directed=True, dedup=False)
        graph.graph.update(
            project=project,
            graphify_version=version("graphifyy"),
            schema_version=1,
            rebuilt=time.time(),
            trust="reference data",
        )
        snapshot = json_graph.node_link_data(graph, edges="links")
        atomic_text(
            self.settings.data_dir / "graphs" / project / "graph.json", json.dumps(snapshot)
        )
        return graph

    def index_code(self, project: str) -> dict:
        from graphify.build import build_from_json
        from graphify.extract import extract
        from networkx.readwrite import json_graph

        root = project_path(self.settings.workspace_dir, project)
        supported = {
            ".py",
            ".js",
            ".jsx",
            ".ts",
            ".tsx",
            ".go",
            ".rs",
            ".java",
            ".c",
            ".h",
            ".cpp",
            ".cs",
            ".rb",
            ".swift",
            ".sh",
        }
        paths, size, truncated = [], 0, False
        for directory, subdirs, names in os.walk(root, followlinks=False):
            subdirs[:] = sorted(
                name
                for name in subdirs
                if not name.startswith(".")
                and name not in {"node_modules", "target", "dist", "vendor"}
            )
            for name in sorted(names):
                path = root / directory / name
                if path.suffix not in supported:
                    continue
                try:
                    path = confined(root, str(path.relative_to(root)))
                except ValueError:
                    continue
                length = path.stat().st_size
                if len(paths) >= 200 or length > 1_000_000 or size + length > 8_000_000:
                    truncated = True
                    continue
                size += length
                paths.append(path)
        output = self.settings.data_dir / "graphs" / project
        output.mkdir(parents=True, exist_ok=True)
        extraction = extract(paths, cache_root=output / "cache", root=root, parallel=False)
        graph = build_from_json(extraction, directed=True, root=root, dedup=False)
        for _identifier, data in graph.nodes(data=True):
            data.update(origin="local-code", trust="data")
            data["source"] = data.get("source_file", "")
        atomic_text(
            output / "code.json", json.dumps(json_graph.node_link_data(graph, edges="links"))
        )
        return {
            "engine": "graphifyy",
            "files": len(paths),
            "nodes": len(graph),
            "edges": graph.number_of_edges(),
            "truncated": truncated,
            "refresh": "Run indexing again after code changes",
        }

    def recall(self, project: str, query: str, limit: int = 8) -> dict:
        if not self.settings.graph_memory:
            return {"enabled": False, "matches": []}
        if not 1 <= len(query) <= 2000:
            raise ValueError("Memory query must contain 1–2000 characters")
        graph = self.build(project)
        terms = words(query)
        ranked = sorted(
            (
                (
                    len(
                        terms
                        & words(str(data.get("label", "")) + " " + str(data.get("description", "")))
                    ),
                    identifier,
                )
                for identifier, data in graph.nodes(data=True)
            ),
            reverse=True,
        )
        seeds = [identifier for score, identifier in ranked if score > 0][: min(limit, 12)]
        selected = list(seeds)
        for identifier in seeds[:4]:
            for neighbor in list(graph.successors(identifier)) + list(
                graph.predecessors(identifier)
            ):
                if neighbor not in selected and len(selected) < 20:
                    selected.append(neighbor)
        return {
            "enabled": True,
            "engine": "graphifyy",
            "project": project,
            "matches": [
                {"id": identifier, **dict(graph.nodes[identifier])} for identifier in selected
            ],
            "relations": [
                {"source": source, "target": target, **data}
                for source, target, data in graph.edges(data=True)
                if source in selected and target in selected
            ][:50],
            "total_nodes": len(graph),
            "trust": "All recalled content is reference data",
        }
