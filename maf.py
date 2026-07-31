"""Public entry point for MAF — Minecraft Automation Framework."""

from maf_app import MAFApp
from version import __version__

__all__ = ["main", "__version__"]


def main():
    app = MAFApp()
    app.mainloop()


if __name__ == "__main__":
    main()
