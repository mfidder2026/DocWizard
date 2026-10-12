"""DocWizard — lokale documentenwizard voor Word met lokale LLM's."""
__version__ = "0.2.1"


def main() -> None:
    from .app import main as _main
    _main()
