"""Ponto de compatibilidade para execução do MS Promoções."""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from promotion_main import main

if __name__ == "__main__":
    main()