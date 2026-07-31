from __future__ import annotations

from functools import lru_cache

from langgraph.graph import END, START, StateGraph

from factor_backend.graph.nodes import (
    node_code_gate,
    node_ingest,
    node_persist,
    node_review_fanout,
    node_review_merge,
    node_step1,
    route_after_gate,
    route_after_step1,
)
from factor_backend.graph.state import GraphState


def build_factor_graph():
    """
    START → ingest → step1 → (review|persist)
         → review_fanout → review_merge → code_gate
         → (revise→step1 | persist) → END
    """
    g = StateGraph(GraphState)
    g.add_node("ingest", node_ingest)
    g.add_node("step1", node_step1)
    g.add_node("review_fanout", node_review_fanout)
    g.add_node("review_merge", node_review_merge)
    g.add_node("code_gate", node_code_gate)
    g.add_node("persist", node_persist)

    g.add_edge(START, "ingest")
    g.add_edge("ingest", "step1")
    g.add_conditional_edges(
        "step1",
        route_after_step1,
        {"review": "review_fanout", "persist": "persist"},
    )
    g.add_edge("review_fanout", "review_merge")
    g.add_edge("review_merge", "code_gate")
    g.add_conditional_edges(
        "code_gate",
        route_after_gate,
        {"revise": "step1", "persist": "persist"},
    )
    g.add_edge("persist", END)
    return g.compile()


@lru_cache
def get_compiled_graph():
    return build_factor_graph()
