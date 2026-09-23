"""VS Code and terminal entry point for K210 Codex Display Hub."""
import sys

from bridge.app import main


if __name__ == "__main__":
    if len(sys.argv) == 1:
        sys.argv.append("--open")
    main()
