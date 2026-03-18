from __future__ import annotations


class ProjectNotInitializedError(ValueError):
    """Raised when a repo has not been initialized in Engram yet."""

    def __init__(self, repo_root: str) -> None:
        self.repo_root = repo_root
        super().__init__(f"project is not initialized: {repo_root}")
