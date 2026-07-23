"""Canvas workflow package."""

__all__ = ["canvas_workflow_runner"]


def __getattr__(name: str):
    if name == "canvas_workflow_runner":
        from app.agent.canvas.workflow.runner import canvas_workflow_runner

        return canvas_workflow_runner
    raise AttributeError(name)
