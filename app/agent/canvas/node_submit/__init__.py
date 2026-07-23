"""Canvas node submit package."""

__all__ = ["collect_submit_material_refs", "prepare_node_submit"]


def __getattr__(name: str):
    if name == "collect_submit_material_refs":
        from app.agent.canvas.node_submit.collect_refs import collect_submit_material_refs

        return collect_submit_material_refs
    if name == "prepare_node_submit":
        from app.agent.canvas.node_submit.prepare import prepare_node_submit

        return prepare_node_submit
    raise AttributeError(name)
