"""python -m myphotos : lance MyPhotos (serveur local sur 127.0.0.1, puis ouverture de la page)."""
import sys

from .serveur import main

if __name__ == '__main__':
    sys.exit(main())
