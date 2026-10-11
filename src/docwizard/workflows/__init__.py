"""Pipelines registry."""
from .chat import auto, chat
from .diagram import diagram
from .documents import add_chapter, enrich, new_document
from .edits import correct, review, translate
from .jobs import JobContext, JobRunner, bus, runner

PIPELINES = {
    "auto": auto,
    "chat": chat,
    "new_document": new_document,
    "add_chapter": add_chapter,
    "translate": translate,
    "correct": correct,
    "review": review,
    "enrich": enrich,
    "diagram": diagram,
}

for _name, _fn in PIPELINES.items():
    runner.register(_name, _fn)

__all__ = ["PIPELINES", "JobContext", "JobRunner", "bus", "runner"]
