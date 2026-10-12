from __future__ import annotations

import argparse
import warnings

from langchain_core._api.deprecation import LangChainPendingDeprecationWarning

warnings.filterwarnings("ignore", category=PendingDeprecationWarning)
warnings.filterwarnings("ignore", category=LangChainPendingDeprecationWarning)
warnings.filterwarnings("ignore", message=".*allowed_objects.*")
warnings.filterwarnings("ignore", category=Warning, module="langgraph.cache.base.*")
from swarm_graph import build_swarm_graph


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the local LangGraph hierarchical swarm proof."
    )
    parser.add_argument(
        "--objective",
        default="Set up a high-volume AI automation contract application lane.",
        help="Objective for the supervisor graph.",
    )
    args = parser.parse_args()

    graph = build_swarm_graph()
    result = graph.invoke(
        {
            "objective": args.objective,
            "plan": [],
            "findings": [],
            "final": "",
        }
    )
    print(result["final"])


if __name__ == "__main__":
    main()
