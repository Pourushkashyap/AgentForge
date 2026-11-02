"""The generation graph (LangGraph), plus a tiny fallback executor.

    research -> master_plan -> frontend_plan -> frontend_build -> api_analysis
      -> backend_plan -> backend_build -> validate
    validate --ok--> finalize
    validate --errors, attempts left--> debug --> validate
    validate --errors, no attempts--> fail
    any node --error--> fail
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from workflow.state import PipelineState, LIST_KEYS, resolve_options

END = "__end__"
ENTRY = "research"

LINEAR = [
    "research", "master_plan", "frontend_plan", "frontend_build",
    "api_analysis", "backend_plan", "backend_build", "validate",
]
NEXT = {name: LINEAR[i + 1] for i, name in enumerate(LINEAR[:-1])}
NODE_NAMES = LINEAR + ["debug", "finalize", "fail"]
MAX_STEPS = 60


def route_after(name: str, state: dict[str, Any]) -> str:
    """Decide the next node. Shared by LangGraph and the fallback runner."""
    if name in ("finalize", "fail"):
        return END
    if state.get("error"):
        return "fail"
    if name == "validate":
        errors = [i for i in state.get("issues") or [] if i.get("severity") == "error"]
        if not errors:
            return "finalize"
        limit = resolve_options(state.get("options"))["max_debug_attempts"]
        fixable = any(i.get("auto_fixable", True) for i in errors)
        if fixable and state.get("debug_attempts", 0) < limit:
            return "debug"
        return "fail"
    if name == "debug":
        return "validate"
    return NEXT[name]


def topology() -> dict[str, Any]:
    """Nodes and edges as data (used by the API and the Mermaid export)."""
    edges = [{"from": a, "to": b, "condition": "ok"} for a, b in NEXT.items()]
    edges += [
        {"from": "validate", "to": "finalize", "condition": "no errors"},
        {"from": "validate", "to": "debug", "condition": "errors, attempts left"},
        {"from": "validate", "to": "fail", "condition": "errors, no attempts left"},
        {"from": "debug", "to": "validate", "condition": "re-check"},
        {"from": "finalize", "to": END, "condition": ""},
        {"from": "fail", "to": END, "condition": ""},
    ]
    edges += [{"from": n, "to": "fail", "condition": "error"}
              for n in LINEAR[:-1] + ["debug"]]
    return {"entry": ENTRY, "nodes": NODE_NAMES, "edges": edges}


def mermaid() -> str:
    lines = ["flowchart TD", "    START([start]) --> research"]
    for edge in topology()["edges"]:
        if edge["condition"] == "error":
            continue  # keep the diagram readable; every node can fail
        target = "DONE([end])" if edge["to"] == END else edge["to"]
        label = f"|{edge['condition']}|" if edge["condition"] not in ("", "ok") else ""
        lines.append(f"    {edge['from']} -->{label} {target}")
    lines.append("    classDef bad fill:#fee,stroke:#c33;")
    lines.append("    class fail bad;")
    return "\n".join(lines)


def _merge(state: dict[str, Any], update: dict[str, Any]) -> None:
    for key, value in update.items():
        if key in LIST_KEYS:
            state[key] = list(state.get(key) or []) + list(value or [])
        else:
            state[key] = value


class FallbackGraph:
    """Same behaviour as the compiled LangGraph, without the dependency."""

    def __init__(self, nodes: dict[str, Callable[..., Awaitable[dict]]]):
        self.nodes = nodes

    async def ainvoke(self, state: dict[str, Any], config: dict | None = None):
        state = dict(state)
        current = ENTRY
        for _ in range(MAX_STEPS):
            _merge(state, await self.nodes[current](state, config))
            current = route_after(current, state)
            if current == END:
                return state
        raise RuntimeError("Graph exceeded the maximum number of steps.")


def build_graph(nodes: dict[str, Callable[..., Awaitable[dict]]] | None = None,
                *, use_langgraph: bool = True):
    """Compile the graph. ``nodes`` lets tests inject fakes."""
    if nodes is None:
        from workflow.nodes import NODES
        nodes = NODES

    if use_langgraph:
        try:
            from langgraph.graph import END as LG_END, StateGraph
        except ImportError:
            use_langgraph = False

    if not use_langgraph:
        return FallbackGraph(nodes)

    graph = StateGraph(PipelineState)
    for name in NODE_NAMES:
        graph.add_node(name, nodes[name])
    graph.set_entry_point(ENTRY)

    for name in NODE_NAMES:
        if name in ("finalize", "fail"):
            graph.add_edge(name, LG_END)
            continue
        targets = {"fail": "fail"}
        if name == "validate":
            targets.update({"finalize": "finalize", "debug": "debug"})
        elif name == "debug":
            targets["validate"] = "validate"
        else:
            targets[NEXT[name]] = NEXT[name]
        graph.add_conditional_edges(
            name, lambda state, n=name: route_after(n, state), targets)
    return graph.compile()


async def run_workflow(
    requirements: str,
    output_dir: str,
    options: dict[str, Any] | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
    graph: Any = None,
) -> dict[str, Any]:
    """Run the graph and return the final state."""
    if not isinstance(requirements, str) or not requirements.strip():
        raise ValueError("Project requirements cannot be empty.")
    graph = graph or build_graph()
    initial: dict[str, Any] = {
        "requirements": requirements.strip(),
        "output_dir": str(output_dir),
        "options": resolve_options(options),
        "issues": [], "fixes": [], "events": [],
        "debug_attempts": 0, "status": "running", "error": None,
    }
    config = {"configurable": {"on_event": on_event}, "recursion_limit": MAX_STEPS}
    return await graph.ainvoke(initial, config)
