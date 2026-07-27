TORTOISE_ORM_MODEL_MODULES = [
    "app.server.generation.persistence.generate_task",
    "app.server.assets.persistence.assets",
    "app.server.chat.persistence.attachments",
    "app.server.chat.persistence.conversations",
    "app.server.chat.persistence.messages",
    "app.server.canvas.persistence.edges",
    "app.server.canvas.persistence.messages",
    "app.server.canvas.persistence.nodes",
    "app.server.canvas.persistence.operations",
    "app.server.canvas.persistence.episode_meta",
    "app.server.canvas.persistence.sessions",
    "app.server.projects.persistence.episodes",
    "app.server.projects.persistence.projects",
    "app.server.auth.persistence.user",
]

__all__ = ["TORTOISE_ORM_MODEL_MODULES"]
