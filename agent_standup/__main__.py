"""Entry point so ``python3 -m agent_standup`` runs the CLI."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
