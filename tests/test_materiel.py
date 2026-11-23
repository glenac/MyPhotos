"""Tests de myphotos/materiel.py : analyse du matériel et conseil du modèle.

    .venv\\Scripts\\python.exe -m unittest tests.test_materiel -v
    .venv\\Scripts\\python.exe tests\\test_materiel.py

Les machines sont fabriquées (dictionnaires « comme analyser() ») ; seul EssaiReel interroge ce PC, et ne lit
aucune photo. La mesure du vrai moteur (EssaiReel.test_mesure_moteur) ne tourne que si MYPHOTOS_TEST_MESURE=1,
pour ne pas fausser les mesures d'autres programmes.
"""
import json
import math
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from myphotos import config, materiel, modele  # noqa: E402

GO = 1 << 30


# ---------------------------------------------------------------- machines fabriquées

def carte(indice, nom, fabricant, dediee, partagee=8.0, directx12=True, logicielle=False):
    return {'indice': indice, 'nom': nom, 'fabricant': fabricant, 'id': None, 'memoire_dediee_go': dediee,
            'memoire_partagee_go': partagee, 'logicielle': logicielle, 'directx12': directx12,
            'integree': None, 'classe': None}


def rendu_logiciel(indice):
    return carte(indice, 'Microsoft Basic Render Driver', 'Microsoft', 0.0, logicielle=True, directx12=None)


def machine(processeur, coeurs, fils, ram, cartes, libre_go=200.0, lecteur='C:', batterie=False, sur_secteur=True,
            build=26100, disponible=None, performants=None, econes=None, avx2=True):
    """Une analyse au format d'analyser(), sans interroger le PC."""
    fabricant = 'AMD' if 'AMD' in processeur else 'Intel'
    nom = materiel._propre(processeur)
    cartes = [dict(c) for c in cartes]
    return {
        'systeme': {'nom': 'Windows 11' if build >= 22000 else 'Windows 10', 'version': '10.0', 'build': build,
                    'revision': 1, 'mise_a_jour': None, 'edition': None, '64bits': True, 'architecture': 'x64',
                    'compatible': build >= 17763},
        'processeur': {'nom': nom, 'fabricant': fabricant, 'coeurs': coeurs, 'fils': fils,
                       'coeurs_performants': performants, 'coeurs_econes': econes, 'frequence_mhz': None,
                       'avx2': avx2, 'graphique_integre': materiel._graphique_integre(nom, fabricant)},
        'memoire': {'totale_go': round(ram * 0.97, 1), 'installee_go': float(ram),
                    'disponible_go': disponible if disponible is not None else round(ram * 0.5, 1)},
        'cartes': cartes,
        'directml': any(c['directx12'] is True and not c['logicielle'] for c in cartes),
        'disque': {'dossier': lecteur + r'\Users\Famille\AppData\Local\MyPhotos', 'lecteur': lecteur,
                   'libre_go': libre_go, 'total_go': 500.0},
        'alimentation': {'batterie': batterie, 'sur_secteur': sur_secteur, 'charge_pourcent': 80 if batterie else None},
        'onnxruntime': {'version': '1.24.4', 'fournisseurs': ['DmlExecutionProvider', 'CPUExecutionProvider']},
        'nb_photos': None,
        'duree_analyse_s': 0.1,
    }


def vieux_portable_4go(**kw):
    # Celeron de 2014, 2 cœurs ; sa puce Intel HD ne fait pas de Direct3D 12 : aucune carte utilisable.
    return machine('Intel(R) Celeron(R) CPU  N2840  @ 2.16GHz', 2, 2, 4,
                   [carte(0, 'Intel(R) HD Graphics', 'Intel', 0.06, 1.9, directx12=False), rendu_logiciel(1)],
                   batterie=True, sur_secteur=False, build=19045, disponible=1.1, avx2=False, **kw)


def portable_iris_16go(**kw):
    return machine('11th Gen Intel(R) Core(TM) i7-1165G7 @ 2.80GHz', 4, 8, 16,
                   [carte(0, 'Intel(R) Iris(R) Xe Graphics', 'Intel', 0.125, 7.8), rendu_logiciel(1)],
                   batterie=True, sur_secteur=True, **kw)


def pc_rx6600xt_32go(**kw):
    return machine('AMD Ryzen 5 5600X 6-Core Processor', 6, 12, 32,
                   [carte(0, 'AMD Radeon RX 6600 XT', 'AMD', 7.98, 16.0), rendu_logiciel(1)], **kw)


def pc_rtx4090_64go(**kw):
    # L'écran est branché sur la carte mère : la puce Intel passe en premier (indice 0) et la RTX 4090 en second.
    return machine('13th Gen Intel(R) Core(TM) i9-13900K', 24, 32, 64,
                   [carte(0, 'Intel(R) UHD Graphics 770', 'Intel', 0.125, 31.9),
                    carte(1, 'NVIDIA GeForce RTX 4090', 'NVIDIA', 23.6, 31.9), rendu_logiciel(2)],
                   performants=8, econes=16, **kw)


def disque_plein(**kw):
    return pc_rx6600xt_32go(libre_go=0.4, **kw)


# ---------------------------------------------------------------- outils de test

class Base(unittest.TestCase):
    """Chaque test voit un dossier de modèles vide (rien n'est « déjà téléchargé ») et le catalogue réel (les quatre
    modèles de modele.MODELES_DISPO)."""

    def setUp(self):
        self.dossier = tempfile.TemporaryDirectory()
        patch = mock.patch.object(config, 'MODELES', Path(self.dossier.name))
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(self.dossier.cleanup)

    def catalogue_base_seul(self):
        """Réduit modele.MODELES_DISPO au modèle standard le temps du test."""
        patch = mock.patch.dict(modele.MODELES_DISPO, {'base': modele.MODELES_DISPO['base']}, clear=True)
        patch.start()
        self.addCleanup(patch.stop)

    def verifier_forme(self, r):
        for champ in ('cle', 'nom', 'description', 'vision', 'texte', 'materiel', 'telechargement_octets',
                      'debit_estime', 'duree_estimee_s', 'duree_texte', 'possible', 'provisoire', 'raisons', 'alertes',
                      'bloquant', 'raison_blocage', 'nb_photos', 'alternatives'):
            self.assertIn(champ, r)
        self.assertIn(r['materiel'], ('carte', 'processeur'))
        self.assertIn(r['vision'], ('fp16', 'fp32'))
        self.assertTrue(r['raisons'])
        self.assertTrue(all(isinstance(t, str) and t for t in r['raisons'] + r['alertes']))
        json.dumps(r, ensure_ascii=False)                    # l'interface le reçoit en JSON
        for alt in r['alternatives']:
            self.assertNotEqual(alt['cle'], r['cle'])
            self.assertTrue(alt['compromis'])
            self.assertIn(alt['materiel'], ('carte', 'processeur'))


# ---------------------------------------------------------------- les cinq machines demandées

class Machines(Base):

    def test_vieux_portable_4go_sans_carte(self):
        a = vieux_portable_4go()
        r = materiel.recommander(a, 7100)                   # 2 cœurs, 4 Go, pas de carte : « rapide »
        self.verifier_forme(r)
        self.assertEqual((r['cle'], r['materiel']), ('rapide', 'processeur'))
        self.assertEqual(r['vision'], 'fp16')               # la vision fp32 demanderait 5 Go de mémoire vive
        self.assertFalse(r['bloquant'])
        self.assertIn("n'a pas de carte graphique utilisable", r['raisons'][0])
        self.assertIn('vos 7 100 photos se fera avec le processeur', r['raisons'][0])
        self.assertTrue(any('peu de mémoire vive' in t for t in r['alertes']))
        self.assertTrue(any('batterie' in t for t in r['alertes']))
        possibles = {alt['cle']: alt['possible'] for alt in r['alternatives']}
        self.assertEqual(possibles, {'base': True, 'precis': False, 'tres_precis': False})   # 4 Go : trop peu
        base = next(alt for alt in r['alternatives'] if alt['cle'] == 'base')
        self.assertGreater(base['duree_estimee_s'], 2 * 3600)              # plus de 2 heures sur un Celeron
        self.assertLess(r['duree_estimee_s'], base['duree_estimee_s'] / 2.5)
        self.assertTrue(base['compromis'].startswith('Plus précis, mais'))

        # Catalogue réduit au modèle standard : on le conseille quand même, sans autre choix.
        self.catalogue_base_seul()
        r = materiel.recommander(a, 7100)
        self.verifier_forme(r)
        self.assertEqual((r['cle'], r['materiel']), ('base', 'processeur'))
        self.assertEqual(r['alternatives'], [])
        self.assertTrue(any('longue' in t for t in r['alertes']))

    def test_portable_iris_16go(self):
        for base_seul in (False, True):
            if base_seul:
                self.catalogue_base_seul()
            r = materiel.recommander(portable_iris_16go(), 7100)
            self.verifier_forme(r)
            self.assertEqual(r['cle'], 'base')                      # puce intégrée récente : standard
            self.assertEqual(r['materiel'], 'carte')                # plus rapide que le processeur bridé du portable
            self.assertEqual(r['indice_carte'], 0)
            self.assertEqual(r['vision'], 'fp16')
            self.assertIn('puce graphique intégrée', r['raisons'][0])
            self.assertFalse(any('batterie' in t for t in r['alertes']))     # branché sur secteur
            self.assertFalse(r['bloquant'])

    def test_pc_rx6600xt_32go(self):
        a = pc_rx6600xt_32go()
        # Carte dédiée de 8 Go et 32 Go de mémoire vive : « precis », qui analyse 7 100 photos en moins de 30 minutes.
        r = materiel.recommander(a, 7100)
        self.verifier_forme(r)
        self.assertEqual((r['cle'], r['materiel'], r['vision'], r['indice_carte']), ('precis', 'carte', 'fp16', 0))
        self.assertEqual(r['raisons'][0], "Votre ordinateur a une carte graphique AMD Radeon RX 6600 XT : "
                                          "l'analyse de vos 7 100 photos prendra environ 6 minutes.")
        self.assertAlmostEqual(r['duree_estimee_s'], 7100 / 20, delta=2)              # mesuré : 20 photos/s
        self.assertTrue(any('peut utiliser le modèle « Précis »' in t for t in r['raisons']))
        self.assertFalse(r['provisoire'])
        self.assertEqual(r['alertes'], [])
        tres = next(alt for alt in r['alternatives'] if alt['cle'] == 'tres_precis')
        self.assertTrue(tres['possible'])
        self.assertTrue(tres['compromis'].startswith('À peine plus précis, mais'))
        self.assertIn('gros téléchargement', tres['compromis'])

        # « tres_precis » n'est jamais conseillé d'office, même déjà téléchargé.
        vraie_taille = modele.taille_telechargement
        with mock.patch.object(modele, 'taille_telechargement',
                               lambda cle, *x, **k: 0 if cle == 'tres_precis' else vraie_taille(cle, *x, **k)):
            self.assertEqual(materiel.recommander(a, 7100)['cle'], 'precis')

        # Bibliothèque immense : « precis » dépasserait la demi-heure, on reste au standard.
        r = materiel.recommander(a, 200_000)
        self.assertEqual((r['cle'], r['materiel']), ('base', 'carte'))
        self.assertTrue(any('« Précis » trouverait mieux' in t for t in r['raisons']))

        # Catalogue réduit au modèle standard.
        self.catalogue_base_seul()
        r = materiel.recommander(a, 7100)
        self.verifier_forme(r)
        self.assertEqual((r['cle'], r['materiel'], r['vision'], r['indice_carte']), ('base', 'carte', 'fp16', 0))
        self.assertEqual(r['raisons'][0], "Votre ordinateur a une carte graphique AMD Radeon RX 6600 XT : "
                                          "l'analyse de vos 7 100 photos prendra environ 2 minutes.")
        self.assertAlmostEqual(r['duree_estimee_s'], 7100 / (4 / 0.06), delta=2)    # 4 fils de lecture, 60 ms la photo
        self.assertEqual(r['telechargement_octets'], modele.taille_telechargement('base', 'fp16', 'fp16'))
        self.assertEqual(r['alertes'], [])

    def test_pc_rtx4090_64go(self):
        a = pc_rtx4090_64go()
        r = materiel.recommander(a, 7100)
        self.verifier_forme(r)
        self.assertEqual((r['cle'], r['materiel'], r['indice_carte']), ('precis', 'carte', 1))   # la RTX, pas l'Intel
        self.assertIn('NVIDIA GeForce RTX 4090', r['raisons'][0])
        self.assertTrue(any('peut utiliser le modèle « Précis »' in t for t in r['raisons']))
        self.assertIn('tres_precis', [alt['cle'] for alt in r['alternatives']])            # proposé, pas conseillé

        # Avec une carte aussi rapide, ce sont les 4 fils de lecture des photos qui limitent le débit.
        self.catalogue_base_seul()
        r = materiel.recommander(a, 7100)
        self.assertEqual((r['cle'], r['materiel'], r['indice_carte']), ('base', 'carte', 1))
        self.assertAlmostEqual(r['debit_estime'], 4 / 0.06, delta=0.1)
        self.assertEqual(r['duree_texte'], 'environ 2 minutes')

    def test_disque_plein(self):
        r = materiel.recommander(disque_plein(), 7100)
        self.verifier_forme(r)
        self.assertTrue(r['bloquant'])
        self.assertIn('pas assez de place sur le disque C:', r['raison_blocage'])
        self.assertEqual(r['alertes'][0], r['raison_blocage'])                # la bloquante d'abord
        self.assertTrue(all(alt['compromis'].startswith('Impossible') for alt in r['alternatives']))

        # Assez de place pour le standard, pas pour « precis » : repli sur le standard, et on le dit.
        r = materiel.recommander(pc_rx6600xt_32go(libre_go=2.0), 7100)
        self.assertEqual(r['cle'], 'base')
        self.assertFalse(r['bloquant'])
        self.assertTrue(any('« Précis » aurait été conseillé' in t and 'disque C:' in t for t in r['raisons']))
        self.assertTrue(any(t.startswith('Il reste peu de place sur le disque C:') for t in r['alertes']))

        # Modèle déjà téléchargé : il ne faut plus que la place des miniatures.
        with mock.patch.object(modele, 'taille_telechargement', lambda *x, **k: 0):
            r = materiel.recommander(pc_rx6600xt_32go(libre_go=1.5), 7100)
        self.assertFalse(r['bloquant'])


# ---------------------------------------------------------------- règles particulières

class Regles(Base):

    def test_jamais_un_modele_qui_ne_tient_pas_en_memoire(self):
        # Grosse carte mais 4 Go de mémoire vive : « precis » et « tres_precis » (5 Go au moins) sont exclus.
        a = machine('AMD Ryzen 5 3600 6-Core Processor', 6, 12, 4,
                    [carte(0, 'NVIDIA GeForce RTX 3060', 'NVIDIA', 12.0)])
        r = materiel.recommander(a, 7100)
        self.assertEqual((r['cle'], r['materiel']), ('base', 'carte'))
        self.assertTrue(r['possible'])
        for alt in r['alternatives']:
            if alt['cle'] in ('precis', 'tres_precis'):
                self.assertFalse(alt['possible'])
                self.assertTrue(alt['bloquant'])
                self.assertTrue(alt['compromis'].startswith('Impossible'))
        # 2 Go : rien ne tient ; on rend quand même un conseil, bloquant, sans exception.
        a = machine('Intel(R) Atom(TM) CPU Z3735F @ 1.33GHz', 4, 4, 2, [])
        r = materiel.recommander(a, 100)
        self.assertTrue(r['bloquant'])
        self.assertFalse(r['possible'])
        self.assertIn('mémoire vive', r['raison_blocage'])
        self.assertEqual(r['raisons'], ["Pour l'instant, aucun modèle ne peut être installé sur cet ordinateur."])
        self.assertTrue(all(not alt['possible'] for alt in r['alternatives']))

    def test_petite_carte_et_peu_de_memoire(self):
        # Portable de 6 Go avec une petite carte (MX150, 2 Go) : le standard, sur la MX150 plutôt que sur l'UHD 620.
        a = machine('Intel(R) Core(TM) i5-8250U CPU @ 1.60GHz', 4, 8, 6,
                    [carte(0, 'Intel(R) UHD Graphics 620', 'Intel', 0.125, 2.9),
                     carte(1, 'NVIDIA GeForce MX150', 'NVIDIA', 2.0)], batterie=True)
        r = materiel.recommander(a, 3000)
        self.assertEqual((r['cle'], r['materiel'], r['indice_carte']), ('base', 'carte', 1))
        self.assertIn('NVIDIA GeForce MX150', r['raisons'][0])
        precis = next(alt for alt in r['alternatives'] if alt['cle'] == 'precis')
        self.assertEqual(precis['materiel'], 'processeur')        # 2 Go sur la carte : trop peu pour « precis »

    def test_regles_sans_carte(self):
        # Pas de carte utilisable : le standard à partir de 6 cœurs et 8 Go de mémoire vive, sinon « rapide ».
        for coeurs, fils, ram, attendu in ((6, 12, 8, 'base'), (4, 8, 16, 'rapide'), (8, 16, 6, 'rapide'),
                                           (12, 24, 32, 'base')):
            with self.subTest(coeurs=coeurs, ram=ram):
                a = machine('AMD Ryzen 5 5600 6-Core Processor', coeurs, fils, ram, [rendu_logiciel(0)])
                r = materiel.recommander(a, 5000)
                self.assertEqual((r['cle'], r['materiel']), (attendu, 'processeur'))
        # Processeur hybride : 2 cœurs performants et 8 économes comptent pour 6.
        a = machine('12th Gen Intel(R) Core(TM) i5-1235U', 10, 12, 16, [rendu_logiciel(0)], performants=2, econes=8)
        self.assertEqual(materiel.recommander(a, 5000)['cle'], 'base')

    def test_tres_precis_jamais_conseille_d_office(self):
        for i, a in enumerate((pc_rx6600xt_32go(), pc_rtx4090_64go())):
            for n in (100, 7100, 50_000):
                with self.subTest(machine=i, n=n):
                    r = materiel.recommander(a, n)
                    self.assertIn(r['cle'], ('precis', 'base'))
                    self.assertIn('tres_precis', [alt['cle'] for alt in r['alternatives']])

    def test_processeur_seul_gros(self):
        # 8 cœurs, 16 Go, pas de carte : standard (pas « rapide »), en fp32 si la place le permet.
        a = machine('AMD Ryzen 7 5700X 8-Core Processor', 8, 16, 16, [rendu_logiciel(0)])
        r = materiel.recommander(a, 7100)
        self.assertEqual((r['cle'], r['materiel'], r['vision']), ('base', 'processeur', 'fp32'))
        self.assertTrue(any('Version complète' in t for t in r['raisons']))
        self.assertTrue(any('Aucune carte graphique' in t for t in r['alertes']))
        # Peu de photos : fp32 ne ferait pas gagner une minute, on garde le téléchargement léger.
        self.assertEqual(materiel.recommander(a, 50)['vision'], 'fp16')
        # Disque presque plein : pas de fp32.
        self.assertEqual(materiel.recommander(machine('AMD Ryzen 7 5700X 8-Core Processor', 8, 16, 16,
                                                      [rendu_logiciel(0)], libre_go=5.5), 7100)['vision'], 'fp16')

    def test_puce_ancienne_moins_rapide_que_le_processeur(self):
        # Bureau 6 cœurs avec une vieille puce Intel HD 630 : le processeur va plus vite qu'elle.
        a = machine('Intel(R) Core(TM) i5-8500 CPU @ 3.00GHz', 6, 6, 16,
                    [carte(0, 'Intel(R) UHD Graphics 630', 'Intel', 0.125, 7.9)])
        r = materiel.recommander(a, 7100)
        self.assertEqual((r['cle'], r['materiel']), ('base', 'processeur'))
        self.assertIn('ne serait pas plus rapide', r['raisons'][0])
        self.assertEqual(r['fils'], 6)                     # 6 cœurs sans hyperthreading : 6 fils, pas 3

    def test_sans_nombre_de_photos(self):
        r = materiel.recommander(pc_rx6600xt_32go())
        self.assertIsNone(r['duree_estimee_s'])
        self.assertIsNone(r['nb_photos'])
        self.assertTrue(any('photos par seconde' in t for t in r['raisons']))
        # Le nombre donné à analyser() sert de valeur par défaut.
        a = pc_rx6600xt_32go()
        a['nb_photos'] = 1200
        self.assertEqual(materiel.recommander(a)['nb_photos'], 1200)

    def test_cles_absentes_du_catalogue_ignorees(self):
        self.catalogue_base_seul()
        self.assertIsNone(materiel.exigences('rapide'))
        self.assertIsNotNone(materiel.exigences('base'))
        r = materiel.recommander(pc_rx6600xt_32go(), 7100)
        self.assertEqual(r['cle'], 'base')
        self.assertEqual(r['alternatives'], [])

    def test_modele_inconnu_deduit_de_sa_fiche(self):
        geant = dict(modele.MODELES_DISPO['tres_precis'], nom='Géant', taille_image=384, rappel_fr=86.5,
                     depot='onnx-community/siglip2-so400m-patch16-384-ONNX')
        with mock.patch.dict(modele.MODELES_DISPO, {'geant': geant}):
            e = materiel.exigences('geant')
            self.assertTrue(e['deduit'] and e['a_confirmer'] and e['carte_conseillee'])
            # 384 px au lieu de 224 et une vision 4,6 fois plus lourde : environ 13 fois plus lent que « base ».
            self.assertLess(e['debits']['carte_dediee'], 92 / 10)
            self.assertGreater(e['memoire_min_go']['processeur'], materiel.EXIGENCES['base']['memoire_min_go']['processeur'])
            r = materiel.recommander(pc_rx6600xt_32go(), 7100)
            self.assertEqual(r['cle'], 'precis')
            self.assertIn('geant', [alt['cle'] for alt in r['alternatives']])

    def test_exigences_coherentes_avec_les_mesures(self):
        # Les minimums écrits dans EXIGENCES découlent des pics de mémoire mesurés.
        for cle, e in materiel.EXIGENCES.items():
            for usage, pic in e['memoire_moteur_go'].items():
                with self.subTest(cle=cle, usage=usage):
                    self.assertEqual(e['memoire_min_go'][usage], materiel._minimum_go(pic))
                    self.assertEqual(e['memoire_conseillee_go'][usage], materiel._conseillee_go(pic))
            self.assertEqual(e['memoire_video_min_go'], materiel._video_min_go(e['memoire_video_go']))
            self.assertFalse(e['a_confirmer'])                        # les quatre modèles ont été mesurés
        # Du moins au plus précis : de plus en plus lent.
        cles = ['rapide', 'base', 'precis', 'tres_precis']
        for a, b in zip(cles, cles[1:]):
            for classe in ('carte_dediee', 'processeur_fp16', 'processeur_fp32'):
                self.assertGreater(materiel.EXIGENCES[a]['debits'][classe], materiel.EXIGENCES[b]['debits'][classe])

    def test_classes_de_cartes(self):
        apu = {'fabricant': 'AMD', 'graphique_integre': True}
        intel = {'fabricant': 'Intel', 'graphique_integre': True}
        cas = [  # (nom, fabricant, mémoire dédiée, processeur, intégrée attendue, classe attendue)
            ('AMD Radeon(TM) Graphics', 'AMD', 0.5, apu, True, 'integree_ancienne'),
            ('AMD Radeon 780M Graphics', 'AMD', 4.0, apu, True, 'integree_recente'),
            ('AMD Radeon RX 7600S', 'AMD', 8.0, apu, False, 'carte_dediee'),
            ('AMD Radeon RX 6600 XT', 'AMD', 7.98, {'fabricant': 'AMD', 'graphique_integre': False}, False, 'carte_dediee'),
            ('AMD Radeon RX 7900 XTX', 'AMD', 24.0, intel, False, 'carte_tres_puissante'),
            ('Intel(R) Iris(R) Xe Graphics', 'Intel', 0.125, intel, True, 'integree_recente'),
            ('Intel(R) Arc(TM) Graphics', 'Intel', 0.125, intel, True, 'integree_recente'),
            ('Intel(R) UHD Graphics 620', 'Intel', 0.125, intel, True, 'integree_ancienne'),
            ('Intel(R) Arc(TM) A770 Graphics', 'Intel', 16.0, apu, False, 'carte_dediee'),
            ('NVIDIA GeForce GTX 1650', 'NVIDIA', 4.0, intel, False, 'carte_entree'),
            ('NVIDIA GeForce RTX 3050 Laptop GPU', 'NVIDIA', 4.0, intel, False, 'carte_entree'),
            ('NVIDIA GeForce RTX 4070 SUPER', 'NVIDIA', 12.0, intel, False, 'carte_puissante'),
            ('NVIDIA GeForce RTX 4090', 'NVIDIA', 23.6, intel, False, 'carte_tres_puissante'),
            ('NVIDIA RTX A2000', 'NVIDIA', 6.0, intel, False, 'carte_dediee'),
        ]
        for nom, fabricant, dediee, proc, integree, classe in cas:
            with self.subTest(nom=nom):
                c = carte(0, materiel._propre(nom), fabricant, dediee)
                c['integree'] = materiel._est_integree(c, proc)
                self.assertEqual(c['integree'], integree)
                self.assertEqual(materiel._classe_carte(c), classe)

    def test_graphique_integre_du_processeur(self):
        cas = [('AMD Ryzen 5 5600X 6-Core Processor', 'AMD', False),
               ('AMD Ryzen 5 5600G with Radeon Graphics', 'AMD', True),
               ('AMD Ryzen 7 7840HS w/ Radeon 780M Graphics', 'AMD', True),
               ('AMD Ryzen 9 7950X 16-Core Processor', 'AMD', True),
               ('AMD Ryzen 5 7500F 6-Core Processor', 'AMD', False),
               ('AMD Ryzen Threadripper 7980X 64-Cores', 'AMD', False),
               ('Intel Core i5-12400F', 'Intel', False),
               ('Intel Core i7-1165G7 @ 2.80GHz', 'Intel', True)]
        for nom, fabricant, attendu in cas:
            with self.subTest(nom=nom):
                self.assertEqual(materiel._graphique_integre(nom, fabricant), attendu)


# ---------------------------------------------------------------- robustesse : jamais d'exception

class Robustesse(Base):

    def test_analyses_abimees(self):
        for a in (None, {}, 'nimporte', 42, {'cartes': 'x', 'memoire': [], 'processeur': None},
                  {'cartes': [None, 3, {'nom': 5, 'memoire_dediee_go': 'beaucoup', 'directx12': True}]},
                  {'memoire': {'totale_go': float('nan')}, 'disque': {'libre_go': -1}}):
            with self.subTest(analyse=a):
                r = materiel.recommander(a, 7100)
                self.verifier_forme(r)
                self.assertEqual(r['cle'], 'base')
        self.assertIsNotNone(materiel.recommander(pc_rx6600xt_32go(), 'beaucoup'))

    def test_conseil_de_secours(self):
        with mock.patch.object(materiel, '_recommander', side_effect=RuntimeError('panne')):
            r = materiel.recommander(pc_rx6600xt_32go(), 7100)
        self.verifier_forme(r)
        self.assertEqual((r['cle'], r['materiel']), ('base', 'carte'))   # le moteur se repliera seul si besoin

    def test_analyser_ne_leve_jamais(self):
        with mock.patch.object(materiel, '_cartes', side_effect=OSError('pas de DXGI')):
            a = materiel.analyser()
        self.assertIsNone(a['cartes'])
        self.assertIsNone(a['directml'])
        noms = ('_systeme', '_processeur', '_memoire', '_alimentation', '_cartes', '_disque', '_onnxruntime')
        patchs = [mock.patch.object(materiel, n, side_effect=RuntimeError(n)) for n in noms]
        for p in patchs:
            p.start()
        try:
            a = materiel.analyser(12)
        finally:
            for p in patchs:
                p.stop()
        self.assertEqual(a['nb_photos'], 12)
        self.assertTrue(all(a[k] is None for k in ('systeme', 'processeur', 'memoire', 'cartes', 'disque')))
        self.verifier_forme(materiel.recommander(a, 12))

    def test_disque_absent(self):
        d = materiel._disque(r'Q:\inexistant\MyPhotos' if os.name == 'nt' else '/inexistant/MyPhotos')
        self.assertIsNone(d['libre_go'])
        self.assertTrue(d['dossier'])


# ---------------------------------------------------------------- mise en forme, mesure

class Outils(unittest.TestCase):

    def test_formater_duree(self):
        cas = [(None, None), (-5, None), (float('nan'), None), ('x', None), (0, "moins d'une minute"),
               (30, "moins d'une minute"), (60, 'environ 1 minute'), (118, 'environ 2 minutes'),
               (599, 'environ 10 minutes'), (14 * 60, 'environ 15 minutes'), (52 * 60, 'environ 50 minutes'),
               (59 * 60, 'environ 1 heure'), (75 * 60, 'environ 1 heure 15'), (150 * 60, 'environ 2 heures 30'),
               (5 * 3600, 'environ 5 heures'), (3 * 86400, 'environ 3 jours')]
        for s, attendu in cas:
            with self.subTest(s=s):
                self.assertEqual(materiel.formater_duree(s), attendu)

    def test_formater_taille(self):
        self.assertEqual(materiel.formater_taille(785264785), '749\u00a0Mo')
        self.assertEqual(materiel.formater_taille(1798443748), '1,7\u00a0Go')
        self.assertEqual(materiel.formater_taille(2 * GO), '2\u00a0Go')
        self.assertIsNone(materiel.formater_taille(None))

    def test_mesurer_moteur_factice(self):
        class Factice:
            taille_image = 32
            materiel = 'carte graphique'

            def __init__(self):
                self.formes = []

            def vecteurs_images(self, lot):
                self.formes.append(lot.shape)
                time.sleep(0.002 * len(lot))                 # 500 photos/s
                return lot.reshape(len(lot), -1)[:, :8]

        m = Factice()
        debit = materiel.mesurer(m, n=32, lot=8)
        self.assertTrue(100 < debit < 600, debit)
        self.assertEqual(set(m.formes), {(8, 3, 32, 32)})    # préchauffage à la même forme (DirectML)

        class Panne(Factice):
            def vecteurs_images(self, lot):
                raise RuntimeError('pilote')
        self.assertIsNone(materiel.mesurer(Panne()))
        self.assertIsNone(materiel.mesurer(object()))

        class Lent(Factice):
            def vecteurs_images(self, lot):
                time.sleep(0.05)
                return lot
        t = time.perf_counter()
        self.assertIsNotNone(materiel.mesurer(Lent(), n=10_000, lot=2, duree_max=0.3))
        self.assertLess(time.perf_counter() - t, 2)          # s'arrête au bout de duree_max

    def test_estimer_duree(self):
        a = pc_rx6600xt_32go()
        self.assertAlmostEqual(materiel.estimer_duree(a, 60, 7100, 'carte graphique'), 118, delta=1)
        # Sur le processeur, la préparation des photos s'ajoute au calcul.
        self.assertGreater(materiel.estimer_duree(a, 8.6, 7100, 'processeur'), 7100 / 8.6)
        # Carte très rapide : les 4 fils de lecture de l'indexeur plafonnent (4 / 60 ms = 67 photos/s).
        self.assertAlmostEqual(materiel.estimer_duree(a, 5000, 25_000, 'carte'), 375, delta=1)
        for mauvais in ((a, None, 10), (a, -3, 10), (a, 10, None), (None, 'x', 'y')):
            self.assertIsNone(materiel.estimer_duree(*mauvais))


# ---------------------------------------------------------------- ce PC

def _afficher(titre, objet):
    texte = f'\n--- {titre}\n' + json.dumps(objet, ensure_ascii=False, indent=1)
    codage = getattr(sys.stdout, 'encoding', None) or 'utf-8'
    print(texte.encode(codage, 'replace').decode(codage))


@unittest.skipUnless(os.name == 'nt', 'Windows seulement')
class EssaiReel(unittest.TestCase):

    def test_ce_pc(self):
        t = time.perf_counter()
        a = materiel.analyser(7100)
        duree = time.perf_counter() - t
        _afficher(f'analyser() en {duree:.3f} s', a)
        r = materiel.recommander(a, 7100)
        _afficher('recommander(analyse, 7100)', r)
        self.assertLess(duree, 2.0)
        self.assertTrue(a['systeme']['nom'].startswith('Windows'))
        self.assertGreaterEqual(a['systeme']['build'], 17763)
        self.assertTrue(a['systeme']['64bits'])
        self.assertGreaterEqual(a['processeur']['coeurs'], 1)
        self.assertGreaterEqual(a['processeur']['fils'], a['processeur']['coeurs'])
        self.assertGreater(a['memoire']['totale_go'], 0)
        self.assertGreater(a['disque']['libre_go'], 0)
        self.assertIsInstance(a['cartes'], list)
        self.assertIsInstance(a['directml'], bool)
        json.dumps(a)
        self.assertIn(r['cle'], modele.MODELES_DISPO)
        self.assertTrue(r['raisons'])
        # Seconde analyse (bibliothèques déjà chargées) : bien plus rapide.
        t = time.perf_counter()
        materiel.analyser()
        self.assertLess(time.perf_counter() - t, 1.0)

    @unittest.skipUnless(os.environ.get('MYPHOTOS_TEST_MESURE') == '1', 'MYPHOTOS_TEST_MESURE=1 pour lancer')
    def test_mesure_moteur(self):
        a = materiel.analyser()
        r = materiel.recommander(a, 7100)
        if modele.manquants(r['cle'], modele.fichiers_necessaires(r['cle'], r['vision'], r['texte'])):
            self.skipTest('modèle conseillé pas encore téléchargé')
        moteur = modele.Moteur(r['cle'], vision=r['vision'], texte=r['texte'],
                               materiel=r['materiel'], fils=r['fils'], charger_texte=False)
        debit = materiel.mesurer(moteur, n=32)
        duree = materiel.estimer_duree(a, debit, 7100, moteur.materiel)
        print(f"\nmesuré : {debit} photos/s sur {moteur.materiel} ; estimé avant : {r['debit_estime']} ; "
              f"7 100 photos : {materiel.formater_duree(duree)}")
        self.assertTrue(debit and debit > 0)
        self.assertTrue(math.isfinite(duree))


if __name__ == '__main__':
    unittest.main(verbosity=2)
