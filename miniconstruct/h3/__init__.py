from .validator import validate_prompt

__all__ = ["AssembledPrompt", "assemble_prompt", "validate_prompt"]


def __getattr__(name: str):
    if name in {"AssembledPrompt", "assemble_prompt"}:
        from .builder import AssembledPrompt, assemble_prompt
        return {"AssembledPrompt": AssembledPrompt, "assemble_prompt": assemble_prompt}[name]
    raise AttributeError(name)
