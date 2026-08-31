"""PyInstaller entry. Load killryujin as a package so __main__ relative imports work."""

from killryujin.__main__ import main

if __name__ == "__main__":
    main()
