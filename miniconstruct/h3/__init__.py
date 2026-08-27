from .validator import validate_prompt

__all__ = ["AssembledPrompt", "assemble_prompt", "validate_prompt"]


def __getattr__(name: str):
    """Keep the historic builder exports without pre-importing its helpers.

    This avoids importing ``guide_acquisition`` while ``python -m`` is about
    to execute that module through runpy.
    """
    if name in {"AssembledPrompt", "assemble_prompt"}:
        from .builder import AssembledPrompt, assemble_prompt

        return {"AssembledPrompt": AssembledPrompt, "assemble_prompt": assemble_prompt}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
