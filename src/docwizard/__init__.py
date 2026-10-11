"""DocWizard — lokale documentenwizard voor Word met lokale LLM's."""
__version__ = "0.1.0"


def main() -> None:
    from .app import main as _main
    _main()
