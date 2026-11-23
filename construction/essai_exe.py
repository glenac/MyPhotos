"""Programme d'essai pour la construction de MyPhotos.exe.

« construction\\construire.ps1 --essai » en fait dist\\MyPhotos-essai.exe, avec le même contenu que
MyPhotos.exe, pour vérifier la chaîne de construction après une mise à jour d'onnxruntime, de
PyInstaller ou de Python.

Il charge les mêmes bibliothèques que MyPhotos, fait un vrai calcul avec le moteur (carte graphique
via DirectML, puis processeur), écrit tout dans un journal et s'arrête seul. Il n'ouvre aucune
fenêtre : l'exécutable est construit sans console, d'où le journal.

    MyPhotos-essai.exe --modele <dossier>\\onnx\\vision_model_fp16.onnx [--journal essai.log]
                       [--reseau] [--attendre 30] [--sans-carte]
    python construction\\essai_exe.py --modele ...          (même chose depuis les sources)

--reseau     : télécharge aussi 100 octets du moteur sur Hugging Face (vérifie HTTPS et la reprise).
--attendre N : attend N secondes avant de s'arrêter (essai d'arrêt forcé du processus).
--sans-carte : n'exige pas la carte graphique (PC sans DirectML).

Code de sortie : 0 si tout va bien, 1 sinon. Le journal (.log) et un résumé (.json) sont écrits à
côté de l'exécutable, sauf si --journal indique un autre fichier.
"""
import time

T0 = time.time()                      # premier instant où Python exécute ce programme

import json                           # noqa: E402
import os                             # noqa: E402
import sys                            # noqa: E402
import threading                      # noqa: E402
import traceback                      # noqa: E402
from pathlib import Path              # noqa: E402

GELE = bool(getattr(sys, 'frozen', False))
DOSSIER = Path(sys.executable).resolve().parent if GELE else Path(__file__).resolve().parent

# Fichier minuscule du moteur « base », révision figée (mêmes valeurs que le paquet myphotos).
URL_ESSAI = ('https://huggingface.co/onnx-community/siglip2-base-patch16-224-ONNX/resolve/'
             'ba1f3b0843f24bc5417d38e19c37b287d719b2f4/tokenizer.json')
AGENT = 'MyPhotos/0.1 (essai de construction)'


# ---------------------------------------------------------------- journal

class Journal:
    """Journal texte écrit ligne par ligne (lisible même si le programme est arrêté net) + résumé JSON."""

    def __init__(self, chemin):
        self.chemin = Path(chemin)
        self.chemin.parent.mkdir(parents=True, exist_ok=True)
        self.f = open(self.chemin, 'w', encoding='utf-8', buffering=1)
        self.resume = {'t0': T0, 'etapes': {}, 'mesures': {}}
        self.verrou = threading.Lock()

    def ecrire(self, texte):
        with self.verrou:
            for ligne in str(texte).splitlines() or ['']:
                self.f.write(f'{time.time() - T0:8.3f}  {ligne}\n')

    def mesure(self, nom, valeur):
        self.resume['mesures'][nom] = valeur

    def enregistrer(self):
        self.resume['t_fin'] = time.time()
        tmp = self.chemin.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(self.resume, ensure_ascii=False, indent=1), encoding='utf-8')
        os.replace(tmp, self.chemin.with_suffix('.json'))

    def fermer(self):
        self.f.close()


def lire_arguments(argv):
    args = {'modele': None, 'journal': None, 'reseau': False, 'attendre': 0.0, 'sans_carte': False}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ('--modele', '--journal', '--attendre') and i + 1 < len(argv):
            args[a[2:]] = float(argv[i + 1]) if a == '--attendre' else argv[i + 1]
            i += 2
            continue
        if a == '--reseau':
            args['reseau'] = True
        elif a == '--sans-carte':
            args['sans_carte'] = True
        i += 1
    return args


def trouver_modele(demande):
    """Le fichier vision_model_fp16.onnx : celui demandé, sinon les emplacements habituels."""
    if demande:
        return Path(demande)
    candidats = []
    for var in ('MYPHOTOS_MODELES', 'MYPHOTOS_MODELES'):
        if os.environ.get(var):
            candidats.append(Path(os.environ[var]))
    candidats += [DOSSIER.parent / 'donnees' / 'modele', DOSSIER / 'modele']
    if os.environ.get('LOCALAPPDATA'):
        candidats.append(Path(os.environ['LOCALAPPDATA']) / 'MyPhotos' / 'modele')
    for racine in candidats:
        p = racine / 'base' / 'onnx' / 'vision_model_fp16.onnx'
        if p.is_file():
            return p
    return None


# ---------------------------------------------------------------- Windows (ctypes)

def _k32():
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    k32.GetModuleHandleW.restype = wintypes.HMODULE
    k32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    k32.GetModuleFileNameW.restype = wintypes.DWORD
    k32.GetModuleFileNameW.argtypes = [wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    return k32


def chemin_dll(nom):
    """Chemin complet de la DLL déjà chargée dans le processus, ou None."""
    import ctypes
    k32 = _k32()
    h = k32.GetModuleHandleW(nom)
    if not h:
        return None
    buf = ctypes.create_unicode_buffer(32768)
    n = k32.GetModuleFileNameW(h, buf, len(buf))
    return buf.value[:n] or None


def creation_processus(pid=None):
    """Instant de création du processus (secondes depuis 1970) selon Windows ; None si inaccessible."""
    import ctypes
    from ctypes import wintypes
    k32 = _k32()
    h = k32.GetCurrentProcess() if pid is None else k32.OpenProcess(0x1000, False, pid)
    if not h:
        return None
    try:
        t = [wintypes.FILETIME() for _ in range(4)]
        if not k32.GetProcessTimes(h, *(ctypes.byref(x) for x in t)):
            return None
        return ((t[0].dwHighDateTime << 32) | t[0].dwLowDateTime) / 1e7 - 11644473600
    finally:
        if pid is not None:
            k32.CloseHandle(h)


def memoire_vive():
    import ctypes
    from ctypes import wintypes

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [('dwLength', wintypes.DWORD), ('dwMemoryLoad', wintypes.DWORD)] + [
            (n, ctypes.c_ulonglong) for n in ('total', 'libre', 'tp', 'lp', 'tv', 'lv', 'lev')]

    m = MEMORYSTATUSEX()
    m.dwLength = ctypes.sizeof(m)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
        raise OSError('GlobalMemoryStatusEx a échoué')
    return m.total, m.libre


# ---------------------------------------------------------------- étapes

class Essai:
    def __init__(self, journal, args):
        self.j = journal
        self.args = args
        self.echecs = []
        self.lot = None

    def etape(self, nom, fonction, obligatoire=True):
        self.j.ecrire(f'--- {nom}')
        t = time.perf_counter()
        try:
            detail = fonction() or {}
            ok = True
        except Exception as e:                      # noqa: BLE001 - un essai doit tout consigner
            detail = {'erreur': f'{type(e).__name__}: {e}'}
            self.j.ecrire(traceback.format_exc())
            ok = False
        duree = round(time.perf_counter() - t, 3)
        self.j.resume['etapes'][nom] = {'ok': ok, 'duree_s': duree, 'obligatoire': obligatoire, **detail}
        self.j.ecrire(f'{"OK " if ok else "ÉCHEC"} {nom} ({duree} s) {json.dumps(detail, ensure_ascii=False)}')
        if not ok and obligatoire:
            self.echecs.append(nom)
        self.j.enregistrer()
        return ok

    # -- environnement
    def environnement(self):
        moi = creation_processus()
        parent = creation_processus(os.getppid())
        d = {
            'gele': GELE,
            'python': sys.version.split()[0],
            'meipass': getattr(sys, '_MEIPASS', None),
            'executable': sys.executable,
            'pid': os.getpid(),
            'ppid': os.getppid(),
            'stdout_none': sys.stdout is None,
            'stderr_none': sys.stderr is None,
            'creation_processus': moi,
            'creation_parent': parent,
        }
        # En « un seul fichier », le parent est le lanceur de PyInstaller qui a décompressé le contenu.
        if moi:
            self.j.mesure('python_pret_s', round(T0 - moi, 3))
        return d

    # -- imports
    def imports(self):
        import importlib
        durees = {}
        for nom in ('numpy', 'PIL.Image', 'tokenizers', 'onnxruntime', 'tkinter', 'sqlite3', 'http.server',
                    'ctypes', 'ssl', 'urllib.request', 'hashlib', 'webbrowser', 'concurrent.futures'):
            t = time.perf_counter()
            importlib.import_module(nom)
            durees[nom] = round(time.perf_counter() - t, 3)
        self.j.mesure('imports_s', round(sum(durees.values()), 3))
        self.j.mesure('imports_fin_depuis_t0_s', round(time.time() - T0, 3))
        return {'durees_s': durees}

    # -- ctypes
    def windows(self):
        import ctypes
        total, libre = memoire_vive()
        disque_libre = ctypes.c_ulonglong(0)
        ctypes.windll.kernel32.GetDiskFreeSpaceExW(str(DOSSIER.anchor), ctypes.byref(disque_libre), None, None)
        return {'ram_go': round(total / 2**30, 1), 'ram_libre_go': round(libre / 2**30, 1),
                'disque_libre_go': round(disque_libre.value / 2**30, 1), 'processeurs': os.cpu_count()}

    # -- numpy
    def numpy(self):
        import numpy as np
        rng = np.random.default_rng(0)
        a = rng.standard_normal((256, 768)).astype(np.float32)
        v = a / np.linalg.norm(a, axis=1, keepdims=True)
        s = v @ v.T
        h = v.astype(np.float16).astype(np.float32)
        if not (np.allclose(np.diag(s), 1, atol=1e-5) and np.abs(h - v).max() < 1e-2):
            raise ArithmeticError('calcul numpy incohérent')
        return {'version': np.__version__}

    # -- Pillow : chaque format lu par MyPhotos, en mémoire
    def pillow(self):
        import io

        import numpy as np
        import PIL
        from PIL import Image, ImageOps, features
        formats = {'JPEG': 'RGB', 'PNG': 'RGBA', 'WEBP': 'RGB', 'TIFF': 'RGB', 'BMP': 'RGB', 'GIF': 'P'}
        x = np.linspace(0, 255, 640, dtype=np.uint8)
        y = np.linspace(0, 255, 480, dtype=np.uint8)
        rvb = np.stack([np.tile(x, (480, 1)), np.tile(y[:, None], (1, 640)), np.full((480, 640), 128, np.uint8)], -1)
        source = Image.fromarray(rvb, 'RGB')
        lus = {}
        for fmt, mode in formats.items():
            b = io.BytesIO()
            im = source.convert(mode) if mode != 'P' else source.quantize(64)
            im.save(b, fmt)
            b.seek(0)
            lu = Image.open(b)
            if fmt == 'JPEG':
                lu.draft('RGB', (224, 224))           # décodage réduit, comme modele.ouvrir_image
            lu = ImageOps.exif_transpose(lu).convert('RGB')
            lus[fmt] = lu.size
        # Lot de 2 images synthétiques préparées exactement comme modele.pixels (SigLIP).
        damier = Image.fromarray(((np.indices((480, 640)).sum(0) // 40 % 2) * 255).astype(np.uint8)).convert('RGB')
        lot = [np.asarray(im.resize((224, 224), Image.BILINEAR), dtype=np.float32) * (1 / 127.5) - 1.0
               for im in (source, damier)]
        self.lot = np.stack([a.transpose(2, 0, 1) for a in lot])
        return {'version': PIL.__version__, 'formats_lus': lus,
                'jpeg': features.check_codec('jpg'), 'webp': features.check_module('webp'),
                'avif': features.check_module('avif') if 'avif' in features.modules else None}

    # -- tokenizers
    def tokenizers(self):
        from tokenizers import Tokenizer
        modele = trouver_modele(self.args['modele'])
        fichier = modele.parent.parent / 'tokenizer.json' if modele else None
        if not fichier or not fichier.is_file():
            return {'tokenizer': 'absent, découpage non essayé'}
        t = time.perf_counter()
        tok = Tokenizer.from_file(str(fichier))
        ids = tok.encode('un chien sur la plage', add_special_tokens=False).ids
        return {'jetons': len(ids), 'chargement_s': round(time.perf_counter() - t, 3)}

    # -- onnxruntime : carte graphique (DirectML) puis processeur, même lot, résultats comparés
    def onnxruntime(self):
        import numpy as np
        import onnxruntime as ort
        modele = trouver_modele(self.args['modele'])
        if not modele or not modele.is_file():
            raise FileNotFoundError('vision_model_fp16.onnx introuvable (--modele)')
        fournisseurs = ort.get_available_providers()
        d = {'version': ort.__version__, 'fournisseurs': fournisseurs, 'device': ort.get_device()}
        lot = self.lot if self.lot is not None else np.zeros((2, 3, 224, 224), np.float32)
        sorties = {}
        for materiel in ('carte', 'processeur'):
            so = ort.SessionOptions()
            so.log_severity_level = 3
            if materiel == 'carte':
                if 'DmlExecutionProvider' not in fournisseurs:
                    d['carte'] = 'DmlExecutionProvider absent'
                    continue
                so.enable_mem_pattern = False
                so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
                so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                prov = [('DmlExecutionProvider', {'device_id': 0}), 'CPUExecutionProvider']
            else:
                # fp16 sur processeur : niveau BASIC obligatoire (bogue d'ONNX Runtime 1.24 en niveau complet).
                so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_BASIC
                so.intra_op_num_threads = max(1, (os.cpu_count() or 2) // 2)
                prov = ['CPUExecutionProvider']
            t = time.perf_counter()
            s = ort.InferenceSession(str(modele), sess_options=so, providers=prov)
            charge = time.perf_counter() - t
            entree = s.get_inputs()[0]
            x = lot.astype(np.float16) if entree.type == 'tensor(float16)' else lot
            noms = [o.name for o in s.get_outputs()]
            sortie = 'pooler_output' if 'pooler_output' in noms else noms[-1]
            t = time.perf_counter()
            s.run([sortie], {entree.name: x})                       # premier passage (préparation)
            premier = time.perf_counter() - t
            t = time.perf_counter()
            v = s.run([sortie], {entree.name: x})[0].astype(np.float32)
            second = time.perf_counter() - t
            v /= np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-12)
            if not np.all(np.isfinite(v)):
                raise FloatingPointError(f'{materiel} : vecteurs invalides')
            sorties[materiel] = v
            d[materiel] = {'fournisseur': s.get_providers()[0], 'forme': list(v.shape),
                           'chargement_s': round(charge, 3), 'premier_lot_s': round(premier, 3),
                           'photos_par_s': round(len(x) / second, 1)}
            self.j.mesure(f'session_{materiel}_s', round(charge, 3))
        if 'carte' in sorties and 'processeur' in sorties:
            d['cosinus_carte_processeur_min'] = round(float(np.sum(sorties['carte'] * sorties['processeur'], 1).min()), 5)
        # DirectML doit venir du paquet embarqué, pas de C:\Windows\System32 (version plus ancienne).
        # onnxruntime.dll n'est pas utilisée par Python (le module .pyd contient tout le moteur).
        d['dll'] = {n: chemin_dll(n) for n in ('DirectML.dll', 'onnxruntime_providers_shared.dll', 'onnxruntime.dll',
                                               'msvcp140.dll', 'vcruntime140_1.dll')}
        meipass = getattr(sys, '_MEIPASS', None)
        if meipass:
            dehors = [n for n in ('DirectML.dll', 'onnxruntime_providers_shared.dll')
                      if d['dll'][n] and not os.path.normcase(d['dll'][n]).startswith(os.path.normcase(meipass))]
            d['dll_hors_paquet'] = dehors
            if dehors:
                raise RuntimeError(f'DLL chargées hors du paquet : {dehors}')
        if d.get('carte', {}).get('fournisseur') != 'DmlExecutionProvider' and not self.args['sans_carte']:
            raise RuntimeError(f'la carte graphique n\'a pas été utilisée : {d.get("carte")}')
        if d.get('processeur', {}).get('fournisseur') != 'CPUExecutionProvider':
            raise RuntimeError('la session processeur a échoué')
        if d.get('cosinus_carte_processeur_min', 1) < 0.99:
            raise ArithmeticError('carte et processeur donnent des résultats trop différents')
        return d

    # -- sqlite3
    def sqlite(self):
        import sqlite3
        import tempfile
        c = sqlite3.connect(':memory:')
        c.execute('create table photos (id integer primary key, chemin text, vecteur blob)')
        c.executemany('insert into photos (chemin, vecteur) values (?, ?)', [(f'p{i}.jpg', bytes(16)) for i in range(100)])
        n = c.execute('select count(*) from photos').fetchone()[0]
        try:
            c.execute('create virtual table t using fts5(x)')
            fts5 = True
        except sqlite3.OperationalError:
            fts5 = False
        json1 = c.execute("select json_extract('{\"a\": 2}', '$.a')").fetchone()[0] == 2
        c.close()
        with tempfile.TemporaryDirectory() as dossier:
            f = sqlite3.connect(os.path.join(dossier, 'essai.db'))
            wal = f.execute('pragma journal_mode=wal').fetchone()[0]
            f.close()
        return {'version': sqlite3.sqlite_version, 'lignes': n, 'fts5': fts5, 'json': json1, 'wal': wal}

    # -- http.server : sans console, sys.stderr vaut None et le journal par défaut plante.
    def http(self):
        import http.server
        import urllib.request
        journal = self.j

        class Simple(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                corps = b'ok'
                self.send_response(200)
                self.send_header('Content-Type', 'text/plain')
                self.send_header('Content-Length', str(len(corps)))
                self.end_headers()
                self.wfile.write(corps)

        class Redirige(Simple):
            def log_message(self, format, *args):        # noqa: A002 - signature de la bibliothèque
                journal.ecrire('http : ' + (format % args))

        res = {}
        for nom, classe in (('journal_par_defaut', Simple), ('journal_redirige', Redirige)):
            serveur = http.server.ThreadingHTTPServer(('127.0.0.1', 0), classe)
            fil = threading.Thread(target=serveur.serve_forever, daemon=True)
            fil.start()
            try:
                direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with direct.open(f'http://127.0.0.1:{serveur.server_address[1]}/', timeout=5) as r:
                    res[nom] = 'ok' if r.read() == b'ok' else 'réponse inattendue'
            except Exception as e:                      # noqa: BLE001
                res[nom] = f'{type(e).__name__}: {e}'
            finally:
                serveur.shutdown()
                serveur.server_close()
        if res['journal_redirige'] != 'ok':
            raise RuntimeError(f'serveur local : {res}')
        return res

    # -- tkinter (Tcl/Tk 9 : scripts rangés dans les DLL) ; fenêtre masquée avant d'être affichée
    def tkinter(self):
        import tkinter
        from tkinter import ttk
        tcl = tkinter.Tcl()
        d = {'tcl': tcl.eval('info patchlevel'), 'tcl_library': tcl.eval('info library')}
        racine = tkinter.Tk()
        racine.withdraw()
        try:
            d['tk'] = racine.tk.eval('package require Tk')
            d['tk_library'] = racine.tk.eval('set tk_library')
            d['ttk_themes'] = list(ttk.Style(racine).theme_names())
            ico = Path(getattr(sys, '_MEIPASS', DOSSIER.parent)) / 'construction' / 'MyPhotos.ico'
            if ico.is_file():
                racine.iconbitmap(default=str(ico))
                d['icone'] = 'ok'
        finally:
            racine.destroy()
        return d

    # -- réseau (facultatif) : HTTPS, certificats Windows, redirection vers le CDN, reprise (Range)
    def reseau(self):
        import ssl
        import urllib.request
        req = urllib.request.Request(URL_ESSAI, headers={'User-Agent': AGENT, 'Range': 'bytes=0-99'})
        t = time.perf_counter()
        with urllib.request.urlopen(req, timeout=30) as r:
            corps = r.read()
            statut = r.status
        if statut != 206 or len(corps) != 100:
            raise IOError(f'réponse inattendue : statut {statut}, {len(corps)} octets')
        return {'statut': statut, 'octets': len(corps), 'duree_s': round(time.perf_counter() - t, 3),
                'openssl': ssl.OPENSSL_VERSION}

    # -- données embarquées
    def donnees(self):
        racine = Path(getattr(sys, '_MEIPASS', DOSSIER.parent))
        trouve = {}
        for paquet in ('myphotos', 'myphotos'):
            p = racine / paquet / 'page' / 'index.html'
            if p.is_file():
                trouve['page'] = f'{paquet}/page/index.html'
        if (racine / 'page' / 'index.html').is_file():
            trouve['page'] = 'page/index.html'
        trouve['icone'] = (racine / 'construction' / 'MyPhotos.ico').is_file()
        trouve['licences'] = (racine / 'LICENCES-TIERS.txt').is_file()
        return trouve


def main():
    args = lire_arguments(sys.argv[1:])
    journal = Journal(args['journal'] or (DOSSIER / 'MyPhotos-essai.log'))
    journal.ecrire(f'MyPhotos : essai de construction (gelé={GELE})')
    e = Essai(journal, args)
    e.etape('environnement', e.environnement)
    e.etape('imports', e.imports)
    e.etape('windows', e.windows)
    e.etape('numpy', e.numpy)
    e.etape('pillow', e.pillow)
    e.etape('tokenizers', e.tokenizers, obligatoire=False)
    e.etape('onnxruntime', e.onnxruntime)
    e.etape('sqlite', e.sqlite)
    e.etape('http', e.http)
    e.etape('tkinter', e.tkinter)
    e.etape('donnees', e.donnees, obligatoire=False)
    if args['reseau']:
        e.etape('reseau', e.reseau)
    if args['attendre'] > 0:
        journal.ecrire(f'ATTENTE {args["attendre"]} s')
        journal.enregistrer()
        time.sleep(args['attendre'])
    journal.resume['echecs'] = e.echecs
    journal.resume['resultat'] = 'OK' if not e.echecs else 'ECHEC'
    journal.mesure('total_depuis_t0_s', round(time.time() - T0, 3))
    journal.ecrire(f'RÉSULTAT : {journal.resume["resultat"]} {e.echecs or ""}')
    journal.enregistrer()
    journal.fermer()
    return 0 if not e.echecs else 1


if __name__ == '__main__':
    try:
        code = main()
    except BaseException:                               # noqa: BLE001 - jamais de boîte d'erreur
        try:
            with open(DOSSIER / 'MyPhotos-essai.erreur.log', 'w', encoding='utf-8') as f:
                f.write(traceback.format_exc())
        except OSError:
            pass
        code = 2
    sys.exit(code)
