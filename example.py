import json

from symbolic import extract_graph
from layout import sugiyama_layout, to_ldtk_dict
from aesthetic import build_aesthetic_layer
from llm import LLMController

def main() -> None:
    
    controller = LLMController(
        provider="ollama",
        model="qwen2.5-coder",
    )
    graph = controller.generate("A cave level with 10 platforms, 3 coins, 3 exits")


    node_layouts, ir_level = sugiyama_layout(graph)

    aesthetic_result = build_aesthetic_layer(
        description="Dungeon level with spikes and skeleton guards",
        graph=graph,
        node_layouts=node_layouts,
        ir_level=ir_level,
        seed=42,
    )

    ldtk_project = to_ldtk_dict(ir_level, aesthetic=aesthetic_result.aesthetic)

    with open("level.ldtk", "w", encoding="utf-8") as f:
        json.dump(ldtk_project, f, indent=2)

    print("Wrote level.ldtk")


if __name__ == "__main__":
    main()

