"""Entry point: GUI with no args, CLI otherwise."""

from __future__ import annotations

import sys


def main() -> None:
    argv = sys.argv[1:]
    if not argv or argv[0] in {"gui", "--gui"}:
        from .gui import main as gui_main

        gui_main()
        return
    from .cli import main as cli_main

    cli_main()


if __name__ == "__main__":
    main()
