#!/usr/bin/env python3
"""Point d'entrée de l'hôte natif NineDeo (lancé par le navigateur)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ninedeo.host import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
