"""Essais de myphotos/requete.py : les dates, les lieux et les mots d'une recherche tapée en français (0.5.0).

Sans réseau, sans photo ni moteur : dates de référence fixées (aujourd_hui donné), lieux factices.
    .venv\\Scripts\\python.exe -m unittest tests.test_requete -v
"""
import dataclasses
import random
import sqlite3
import statistics
import sys
import time
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from myphotos import requete                                                        # noqa: E402
from myphotos.requete import (Analyse, FiltreDates, Periode, analyser, contient,    # noqa: E402
                              intervalles, normaliser, paques)

AUJ = date(2026, 11, 26)          # un jeudi
D = date.fromisoformat
TOUS_LES_ANS = 2027 - 1900 + 1    # fête ou mois sans année : de 1900 à l'an prochain (128 périodes)


def setUpModule():
    requete._STRICT = True        # pendant ces essais, une erreur de lecture d'une date remonte au lieu d'être ignorée


def tearDownModule():
    requete._STRICT = False


def bornes(a):
    return [(p.debut, p.fin) for p in a.periodes]


def per(*paires):
    return [(D(x), D(y)) for x, y in paires]


class Lieux:
    """chercher_lieu factice : quelques noms ; les noms qui sont aussi des mots courants (« nice ») seulement avec
    une préposition ou une majuscule ; chaque appel est gardé."""
    NOMS = {'bretagne': 'Bretagne', 'paris': 'Paris', 'saint malo': 'Saint-Malo', 'la baule': 'La Baule',
            'le mans': 'Le Mans', 'aix en provence': 'Aix-en-Provence', 'nantes': 'Nantes', 'lyon': 'Lyon',
            'saintes maries de la mer': 'Saintes-Maries-de-la-Mer', 'champ de mars': 'Champ de Mars',
            'ile de paques': 'Île de Pâques', 'les sables d olonne': "Les Sables-d'Olonne", 'finistere': 'Finistère',
            'brest': 'Brest', 'rome': 'Rome', 'nice': 'Nice', 'avril': 'Avril', 'texas': 'Texas',
            'saint jean pied de port': 'Saint-Jean-Pied-de-Port'}
    AMBIGUS = {'nice'}

    def __init__(self):
        self.appels = []

    def __call__(self, nom, indices):
        self.appels.append((nom, indices))
        if nom not in self.NOMS:
            return None
        if nom in self.AMBIGUS and not (indices['preposition'] or indices['majuscule']):
            return None
        return {'nom': self.NOMS[nom]}

    def noms(self):
        return [n for n, _i in self.appels]

    def indices(self, nom):
        return next(i for n, i in self.appels if n == nom)


def _nom_essai(texte):
    s = normaliser(texte).replace(' ', '_') or 'vide'
    return ''.join(c if c.isascii() and (c.isalnum() or c == '_') else 'x' for c in s)[:48]


def _generer(classe, cas):
    """Un essai par cas : (texte, mots attendus, périodes attendues [(debut, fin)] ou un nombre, libellé ou None)."""
    for k, (texte, mots, attendu, libelle) in enumerate(cas):
        def essai(self, texte=texte, mots=mots, attendu=attendu, libelle=libelle):
            a = analyser(texte, AUJ)
            self.assertEqual(a.mots, mots, texte)
            if isinstance(attendu, int):
                self.assertEqual(len(a.periodes), attendu, (texte, bornes(a)[:3]))
            else:
                self.assertEqual(bornes(a), per(*attendu), texte)
            if libelle is not None:
                self.assertEqual([p['libelle'] for p in a.puces], libelle, texte)
            for p in a.periodes:
                self.assertLessEqual(p.debut, p.fin, texte)
        essai.__doc__ = f'« {texte} »'
        setattr(classe, f'test_{k:03d}_{_nom_essai(texte)}', essai)


# Dates à comprendre (le 26/11/2026) : (texte, mots, périodes ou nombre de périodes, libellés des puces ou None).
CAS_DATES = [
    # années
    ('2019', '', [('2019-01-01', '2019-12-31')], ['2019']),
    ('en 2019', '', [('2019-01-01', '2019-12-31')], ['2019']),
    ('année 2019', '', [('2019-01-01', '2019-12-31')], ['2019']),
    ("l'année 2019", '', [('2019-01-01', '2019-12-31')], ['2019']),
    ('an 2000', '', [('2000-01-01', '2000-12-31')], ['2000']),
    ("en l'an 2000", '', [('2000-01-01', '2000-12-31')], ['2000']),
    ('année 98', '', [('1998-01-01', '1998-12-31')], ['1998']),
    ('1900', '', [('1900-01-01', '1900-12-31')], ['1900']),
    ('2026', '', [('2026-01-01', '2026-12-31')], ['2026']),
    ('de 2018 à 2020', '', [('2018-01-01', '2020-12-31')], ['2018 à 2020']),
    ('de 2018 a 2020', '', [('2018-01-01', '2020-12-31')], ['2018 à 2020']),
    ('entre 2018 et 2020', '', [('2018-01-01', '2020-12-31')], ['2018 à 2020']),
    ('2018-2020', '', [('2018-01-01', '2020-12-31')], ['2018 à 2020']),
    ('2018 - 2020', '', [('2018-01-01', '2020-12-31')], ['2018 à 2020']),
    ('2018 – 2020', '', [('2018-01-01', '2020-12-31')], ['2018 à 2020']),
    ('2018 à 2020', '', [('2018-01-01', '2020-12-31')], ['2018 à 2020']),
    ('2020-2018', '', [('2018-01-01', '2020-12-31')], ['2018 à 2020']),
    ('2018-20', '', [('2018-01-01', '2020-12-31')], ['2018 à 2020']),
    ('2019/20', '', [('2019-01-01', '2020-12-31')], ['2019 à 2020']),
    ('2019 et 2020', '', [('2019-01-01', '2019-12-31'), ('2020-01-01', '2020-12-31')], ['2019', '2020']),
    ('années 90', '', [('1990-01-01', '1999-12-31')], ['années 1990']),
    ('les années 80', '', [('1980-01-01', '1989-12-31')], ['années 1980']),
    ('dans les années 1970', '', [('1970-01-01', '1979-12-31')], ['années 1970']),
    ('années 2000', '', [('2000-01-01', '2009-12-31')], ['années 2000']),
    ('années 2010', '', [('2010-01-01', '2019-12-31')], ['années 2010']),
    ('années 20', '', [('1920-01-01', '1929-12-31'), ('2020-01-01', '2029-12-31')], ['années 1920 ou années 2020']),
    ('années soixante', '', [('1960-01-01', '1969-12-31')], ['années 1960']),
    ('années soixante-dix', '', [('1970-01-01', '1979-12-31')], ['années 1970']),
    ('années quatre-vingt', '', [('1980-01-01', '1989-12-31')], ['années 1980']),
    ('années quatre-vingt-dix', '', [('1990-01-01', '1999-12-31')], ['années 1990']),
    ('début des années 2000', '', [('2000-01-01', '2003-12-31')], ['début des années 2000']),
    ('milieu des années 80', '', [('1983-01-01', '1986-12-31')], ['milieu des années 1980']),
    ('fin des années 90', '', [('1996-01-01', '1999-12-31')], ['fin des années 1990']),
    ('début 2019', '', [('2019-01-01', '2019-04-30')], ['début 2019']),
    ('mi-2019', '', [('2019-05-01', '2019-08-31')], ['mi-2019']),
    ('fin 2019', '', [('2019-09-01', '2019-12-31')], ['fin 2019']),
    ("fin de l'année 2019", '', [('2019-09-01', '2019-12-31')], ['fin 2019']),
    # mois
    ('mars 2021', '', [('2021-03-01', '2021-03-31')], ['mars 2021']),
    ('en mars 2021', '', [('2021-03-01', '2021-03-31')], ['mars 2021']),
    ('Mars 2021', '', [('2021-03-01', '2021-03-31')], ['mars 2021']),
    ('mars 21', '', [('2021-03-01', '2021-03-31')], ['mars 2021']),
    ('mai 68', '', [('1968-05-01', '1968-05-31')], ['mai 1968']),
    ('Juillet 2019', '', [('2019-07-01', '2019-07-31')], ['juillet 2019']),
    ('février 2024', '', [('2024-02-01', '2024-02-29')], ['février 2024']),
    ('février 2023', '', [('2023-02-01', '2023-02-28')], ['février 2023']),
    ("au mois d'août 2019", '', [('2019-08-01', '2019-08-31')], ['août 2019']),
    ('mois de mai 2020', '', [('2020-05-01', '2020-05-31')], ['mai 2020']),
    ('sept 2019', '', [('2019-09-01', '2019-09-30')], ['septembre 2019']),
    ('déc. 2018', '', [('2018-12-01', '2018-12-31')], ['décembre 2018']),
    ('07/2019', '', [('2019-07-01', '2019-07-31')], ['juillet 2019']),
    ('2019-07', '', [('2019-07-01', '2019-07-31')], ['juillet 2019']),
    ('2019-12', '', [('2019-12-01', '2019-12-31')], ['décembre 2019']),
    ('début août 2019', '', [('2019-08-01', '2019-08-10')], ['début août 2019']),
    ('mi-août 2019', '', [('2019-08-10', '2019-08-20')], ['mi-août 2019']),
    ('fin août 2019', '', [('2019-08-20', '2019-08-31')], ['fin août 2019']),
    ('fin février 2024', '', [('2024-02-20', '2024-02-29')], ['fin février 2024']),
    ('mars et avril 2021', '', [('2021-03-01', '2021-03-31'), ('2021-04-01', '2021-04-30')],
     ['mars et avril 2021']),
    ('de mars à mai 2021', '', [('2021-03-01', '2021-05-31')], ['mars à mai 2021']),
    ('entre mars et mai 2021', '', [('2021-03-01', '2021-05-31')], ['mars à mai 2021']),
    ('de novembre 2019 à février 2020', '', [('2019-11-01', '2020-02-29')], ['novembre 2019 à février 2020']),
    ('de novembre à février 2020', '', [('2019-11-01', '2020-02-29')], ['novembre 2019 à février 2020']),
    ('en août', '', TOUS_LES_ANS, ['août (chaque année)']),
    ("au mois d'août", '', TOUS_LES_ANS, ['août (chaque année)']),
    ('fin août', '', TOUS_LES_ANS, ['fin août (chaque année)']),
    ('entre mars et mai', '', TOUS_LES_ANS, ['mars à mai (chaque année)']),
    ('photos de mars', '', TOUS_LES_ANS, ['mars (chaque année)']),
    # saisons
    ('été 2022', '', [('2022-06-01', '2022-09-22')], ['été 2022']),
    ("l'été 2022", '', [('2022-06-01', '2022-09-22')], ['été 2022']),
    ('Printemps 2020', '', [('2020-03-01', '2020-06-20')], ['printemps 2020']),
    ('automne 2019', '', [('2019-09-01', '2019-12-20')], ['automne 2019']),
    ('hiver 2020', '', [('2019-12-01', '2020-03-20'), ('2020-12-01', '2021-03-20')], ['hiver 2020']),
    ('hiver 2019-2020', '', [('2019-12-01', '2020-03-20')], ['hiver 2019-2020']),
    ('hiver 2019/2020', '', [('2019-12-01', '2020-03-20')], ['hiver 2019-2020']),
    ('été 98', '', [('1998-06-01', '1998-09-22')], ['été 1998']),
    ('été et automne 2020', '', [('2020-06-01', '2020-09-22'), ('2020-09-01', '2020-12-20')],
     ['été et automne 2020']),
    ('étés 2021 et 2022', '', [('2021-06-01', '2021-09-22'), ('2022-06-01', '2022-09-22')], ['été 2021 et 2022']),
    ("photos de l'été 2022", '', [('2022-06-01', '2022-09-22')], ['été 2022']),
    ("fin de l'été 2019", '', [('2019-06-01', '2019-09-22')], ['été 2019']),
    ('en été', '', TOUS_LES_ANS, ['été (chaque année)']),
    ("l'été", '', TOUS_LES_ANS, ['été (chaque année)']),
    ('au printemps', '', TOUS_LES_ANS, ['printemps (chaque année)']),
    ("à l'automne", '', TOUS_LES_ANS, ['automne (chaque année)']),
    ('en hiver', '', TOUS_LES_ANS, ['hiver (chaque année)']),
    ('cet été', '', [('2026-06-01', '2026-09-22')], ['été 2026']),
    ("l'été dernier", '', [('2025-06-01', '2025-09-22'), ('2026-06-01', '2026-09-22')], ['été 2026 ou été 2025']),
    ("l'hiver dernier", '', [('2025-12-01', '2026-03-20')], ['hiver 2025-2026']),
    ("l'automne dernier", '', [('2025-09-01', '2025-12-20')], ['automne 2025']),
    # fêtes
    ('Noël 2019', '', [('2019-12-24', '2019-12-26')], ['Noël 2019']),
    ('noel 2019', '', [('2019-12-24', '2019-12-26')], ['Noël 2019']),
    ('NOËL 2019', '', [('2019-12-24', '2019-12-26')], ['Noël 2019']),
    ('Noël 98', '', [('1998-12-24', '1998-12-26')], ['Noël 1998']),
    ('Noël 2019 et 2020', '', [('2019-12-24', '2019-12-26'), ('2020-12-24', '2020-12-26')], ['Noël 2019 et 2020']),
    ('Noël de 2015 à 2017', '', [('2015-12-24', '2015-12-26'), ('2016-12-24', '2016-12-26'),
                                 ('2017-12-24', '2017-12-26')], ['Noël 2015 à 2017']),
    ('Noël 2027', '', [('2027-12-24', '2027-12-26')], ['Noël 2027']),
    ('Noël de 2019', '', [('2019-12-24', '2019-12-26')], ['Noël 2019']),
    ("l'été de 2019", '', [('2019-06-01', '2019-09-22')], ['été 2019']),
    ("l'été de l'an dernier", '', [('2025-06-01', '2025-09-22')], ['été', '2025']),
    ('bonne année 2020', 'bonne année', [('2020-01-01', '2020-12-31')], ['2020']),
    ('voiture 2008', 'voiture', [('2008-01-01', '2008-12-31')], ['2008']),
    ('Peugeot en 2008', 'Peugeot', [('2008-01-01', '2008-12-31')], ['2008']),
    ('réveillon de Noël 2019', '', [('2019-12-24', '2019-12-25')], ['réveillon de Noël 2019']),
    ('veille de Noël 2019', '', [('2019-12-24', '2019-12-25')], ['réveillon de Noël 2019']),
    ('nuit de Noël 2019', '', [('2019-12-24', '2019-12-25')], ['réveillon de Noël 2019']),
    ('jour de Noël 2019', '', [('2019-12-25', '2019-12-25')], ['jour de Noël 2019']),
    ('matin de Noël 2019', '', [('2019-12-25', '2019-12-25')], ['jour de Noël 2019']),
    ('lendemain de Noël 2019', '', [('2019-12-26', '2019-12-26')], ['lendemain de Noël 2019']),
    ('Saint-Sylvestre 2019', '', [('2019-12-31', '2020-01-01')], ['Saint-Sylvestre 2019']),
    ('réveillon du Nouvel An 2019', '', [('2019-12-31', '2020-01-01')], ['Saint-Sylvestre 2019']),
    ('nuit de la Saint-Sylvestre 2019', '', [('2019-12-31', '2020-01-01')], ['Saint-Sylvestre 2019']),
    ('Nouvel An 2020', '', [('2019-12-31', '2020-01-01'), ('2020-12-31', '2021-01-01')], ['Nouvel An 2020']),
    ("jour de l'an 2020", '', [('2019-12-31', '2020-01-01'), ('2020-12-31', '2021-01-01')], ['Nouvel An 2020']),
    ('réveillon 2019', '', [('2019-12-24', '2019-12-25'), ('2019-12-31', '2020-01-01')], ['réveillons 2019']),
    ("fêtes de fin d'année 2019", '', [('2019-12-20', '2020-01-02')], ["fêtes de fin d'année 2019"]),
    ('Pâques 2019', '', [('2019-04-20', '2019-04-22')], ['Pâques 2019']),
    ('paques 2024', '', [('2024-03-30', '2024-04-01')], ['Pâques 2024']),
    ('dimanche de Pâques 2025', '', [('2025-04-20', '2025-04-20')], ['dimanche de Pâques 2025']),
    ('lundi de Pâques 2024', '', [('2024-04-01', '2024-04-01')], ['lundi de Pâques 2024']),
    ('week-end de Pâques 2019', '', [('2019-04-20', '2019-04-22')], ['Pâques 2019']),
    ('samedi de Pâques 2019', '', [('2019-04-20', '2019-04-22')], ['Pâques 2019']),
    ('Ascension 2019', '', [('2019-05-30', '2019-06-02')], ['Ascension 2019']),
    ("pont de l'Ascension 2021", '', [('2021-05-13', '2021-05-16')], ['Ascension 2021']),
    ('Pentecôte 2019', '', [('2019-06-08', '2019-06-10')], ['Pentecôte 2019']),
    ('lundi de Pentecôte 2019', '', [('2019-06-10', '2019-06-10')], ['lundi de Pentecôte 2019']),
    ('14 juillet 2019', '', [('2019-07-13', '2019-07-15')], ['14 juillet 2019']),
    ('le 14 juillet 2019', '', [('2019-07-13', '2019-07-15')], ['14 juillet 2019']),
    ('fête nationale 2019', '', [('2019-07-13', '2019-07-15')], ['14 juillet 2019']),
    ('Toussaint 2020', '', [('2020-10-31', '2020-11-02')], ['Toussaint 2020']),
    ('Halloween 2020', '', [('2020-10-30', '2020-11-01')], ['Halloween 2020']),
    ('Saint-Valentin 2021', '', [('2021-02-13', '2021-02-15')], ['Saint-Valentin 2021']),
    ('St Valentin 2021', '', [('2021-02-13', '2021-02-15')], ['Saint-Valentin 2021']),
    ('fête de la musique 2019', '', [('2019-06-21', '2019-06-22')], ['fête de la musique 2019']),
    ('1er mai 2020', '', [('2020-05-01', '2020-05-01')], ['1er mai 2020']),
    ('fête du travail 2020', '', [('2020-05-01', '2020-05-01')], ['1er mai 2020']),
    ('15 août 2020', '', [('2020-08-15', '2020-08-15')], ['15 août 2020']),
    ('Assomption 2020', '', [('2020-08-15', '2020-08-15')], ['15 août 2020']),
    ('vacances de Noël 2019', '', [('2019-12-19', '2020-01-04')], ['vacances de Noël 2019']),
    ('vacances de la Toussaint 2020', '', [('2020-10-17', '2020-11-05')], ['vacances de la Toussaint 2020']),
    ('vacances de février 2020', '', [('2020-02-03', '2020-03-12')], ["vacances d'hiver 2020"]),
    ('vacances de Pâques 2024', '', [('2024-03-17', '2024-05-10')], ['vacances de printemps 2024']),
    ('vacances de printemps 2019', '', [('2019-04-04', '2019-05-10')], ['vacances de printemps 2019']),
    ('grandes vacances 2022', '', [('2022-06-25', '2022-09-05')], ['grandes vacances 2022']),
    ("vacances d'été 2022", '', [('2022-06-25', '2022-09-05')], ['grandes vacances 2022']),
    ('Noël', '', TOUS_LES_ANS, ['Noël (chaque année)']),
    ('Pâques', '', TOUS_LES_ANS, ['Pâques (chaque année)']),
    ('St-Valentin', '', TOUS_LES_ANS, ['Saint-Valentin (chaque année)']),
    ('14 juillet', '', TOUS_LES_ANS, ['14 juillet (chaque année)']),
    ('réveillon', '', 2 * TOUS_LES_ANS, ['réveillons (chaque année)']),
    ("l'Ascension", '', TOUS_LES_ANS, ['Ascension (chaque année)']),
    ('Ascension', '', TOUS_LES_ANS, ['Ascension (chaque année)']),
    ('Noël dernier', '', [('2025-12-24', '2025-12-26')], ['Noël 2025']),
    ('Pâques dernier', '', [('2025-04-19', '2025-04-21'), ('2026-04-04', '2026-04-06')],
     ['Pâques 2026 ou Pâques 2025']),
    # dates précises
    ('le 3 mai 2020', '', [('2020-05-03', '2020-05-03')], ['3 mai 2020']),
    ('3 mai 2020', '', [('2020-05-03', '2020-05-03')], ['3 mai 2020']),
    ('1er janvier 2020', '', [('2020-01-01', '2020-01-01')], ['1er janvier 2020']),
    ('premier janvier 2020', '', [('2020-01-01', '2020-01-01')], ['1er janvier 2020']),
    ('14/07/2019', '', [('2019-07-14', '2019-07-14')], ['14 juillet 2019']),
    ('14-07-2019', '', [('2019-07-14', '2019-07-14')], ['14 juillet 2019']),
    ('14.07.19', '', [('2019-07-14', '2019-07-14')], ['14 juillet 2019']),
    ('2019-07-14', '', [('2019-07-14', '2019-07-14')], ['14 juillet 2019']),
    ('29 février 2020', '', [('2020-02-29', '2020-02-29')], ['29 février 2020']),
    ('29/02/2024', '', [('2024-02-29', '2024-02-29')], ['29 février 2024']),
    ('14 sept 2019', '', [('2019-09-14', '2019-09-14')], ['14 septembre 2019']),
    ('du 3 au 10 mai 2020', '', [('2020-05-03', '2020-05-10')], ['3 au 10 mai 2020']),
    ('du 28 juin au 3 juillet 2019', '', [('2019-06-28', '2019-07-03')], ['28 juin au 3 juillet 2019']),
    ('du 28 décembre au 3 janvier 2020', '', [('2019-12-28', '2020-01-03')],
     ['28 décembre 2019 au 3 janvier 2020']),
    ('3-10 mai 2020', '', [('2020-05-03', '2020-05-10')], ['3 au 10 mai 2020']),
    ('3 et 4 mai 2020', '', [('2020-05-03', '2020-05-03'), ('2020-05-04', '2020-05-04')], ['3 et 4 mai 2020']),
    ('le 3 mai', '', TOUS_LES_ANS, ['3 mai (chaque année)']),
    ('1er mai', '', TOUS_LES_ANS, ['1er mai (chaque année)']),
    ('29 février', '', 31, ['29 février (chaque année)']),            # 1904 à 2024 : 1900 n'est pas bissextile
    # relatives (le jeudi 26/11/2026)
    ("aujourd'hui", '', [('2026-11-26', '2026-11-26')], ['26 novembre 2026']),
    ('aujourdhui', '', [('2026-11-26', '2026-11-26')], ['26 novembre 2026']),
    ('ce matin', '', [('2026-11-26', '2026-11-26')], ['26 novembre 2026']),
    ('ce soir', '', [('2026-11-26', '2026-11-26')], ['26 novembre 2026']),
    ('hier', '', [('2026-11-25', '2026-11-25')], ['25 novembre 2026']),
    ("photos d'hier", '', [('2026-11-25', '2026-11-25')], ['25 novembre 2026']),
    ('avant-hier', '', [('2026-11-24', '2026-11-24')], ['24 novembre 2026']),
    ('cette nuit', '', [('2026-11-25', '2026-11-26')], ['25 au 26 novembre 2026']),
    ('cette semaine', '', [('2026-11-23', '2026-11-29')], ['23 au 29 novembre 2026']),
    ('la semaine dernière', '', [('2026-11-16', '2026-11-22')], ['16 au 22 novembre 2026']),
    ('la semaine passée', '', [('2026-11-16', '2026-11-22')], ['16 au 22 novembre 2026']),
    ('ce week-end', '', [('2026-11-21', '2026-11-22')], ['21 au 22 novembre 2026']),
    ('le week-end dernier', '', [('2026-11-21', '2026-11-22')], ['21 au 22 novembre 2026']),
    ('ce mois-ci', '', [('2026-11-01', '2026-11-30')], ['novembre 2026']),
    ('le mois dernier', '', [('2026-10-01', '2026-10-31')], ['octobre 2026']),
    ('cette année', '', [('2026-01-01', '2026-12-31')], ['2026']),
    ("l'an dernier", '', [('2025-01-01', '2025-12-31')], ['2025']),
    ("l'année dernière", '', [('2025-01-01', '2025-12-31')], ['2025']),
    ("l'an passé", '', [('2025-01-01', '2025-12-31')], ['2025']),
    ('lundi dernier', '', [('2026-11-23', '2026-11-23')], ['lundi 23 novembre 2026']),
    ('dimanche dernier', '', [('2026-11-22', '2026-11-22')], ['dimanche 22 novembre 2026']),
    ('jeudi dernier', '', [('2026-11-19', '2026-11-19')], ['jeudi 19 novembre 2026']),
    ('il y a 3 jours', '', [('2026-11-23', '2026-11-23')], ['23 novembre 2026']),
    ('il y a 10 jours', '', [('2026-11-15', '2026-11-17')], ['vers le 16 novembre 2026']),
    ('il y a quinze jours', '', [('2026-11-09', '2026-11-13')], ['vers le 11 novembre 2026']),
    ('il y a 2 semaines', '', [('2026-11-09', '2026-11-15')], ['vers le 12 novembre 2026']),
    ('il y a une semaine', '', [('2026-11-16', '2026-11-22')], ['vers le 19 novembre 2026']),
    ('il y a 3 mois', '', [('2026-08-01', '2026-09-10')], ['vers août 2026']),
    ('il y a 2 ans', '', [('2024-01-01', '2025-02-26')], ['vers 2024']),
    ('il y a deux ans', '', [('2024-01-01', '2025-02-26')], ['vers 2024']),
    ('il y a un an', '', [('2025-01-01', '2026-02-26')], ['vers 2025']),
    ('il y a 100 ans', '', [('1926-01-01', '1927-02-26')], ['vers 1926']),
    ('il y a quelques jours', '', [('2026-11-16', '2026-11-25')], ['16 au 25 novembre 2026']),
    ('il y a quelques semaines', '', [('2026-10-01', '2026-11-19')], ['1er octobre au 19 novembre 2026']),
    ('il y a quelques mois', '', [('2026-02-26', '2026-10-26')], ['26 février au 26 octobre 2026']),
    ('il y a quelques années', '', [('2018-01-01', '2024-12-31')], ['2018 à 2024']),
    # depuis, avant, après, vers
    ('depuis 2019', '', [('2019-01-01', '2026-11-26')], ['depuis 2019']),
    ('à partir de 2015', '', [('2015-01-01', '2026-11-26')], ['depuis 2015']),
    ('avant 2010', '', [('1900-01-01', '2009-12-31')], ['avant 2010']),
    ('après 2015', '', [('2016-01-01', '2026-11-26')], ['après 2015']),
    ("jusqu'en 2010", '', [('1900-01-01', '2010-12-31')], ["jusqu'en 2010"]),
    ('vers 2010', '', [('2009-01-01', '2011-12-31')], ['vers 2010']),
    ('depuis mars 2021', '', [('2021-03-01', '2026-11-26')], ['depuis mars 2021']),
    ('avant le 3 mai 2020', '', [('1900-01-01', '2020-05-02')], ['avant le 3 mai 2020']),
    ("après l'été 2022", '', [('2022-09-23', '2026-11-26')], ['après le 22 septembre 2022']),
    ('avant Noël 2019', '', [('1900-01-01', '2019-12-23')], ['avant le 24 décembre 2019']),
    ('depuis hier', '', [('2026-11-25', '2026-11-26')], ['depuis le 25 novembre 2026']),
    ('depuis 3 mois', '', [('2026-08-26', '2026-11-26')], ['depuis le 26 août 2026']),
    ('depuis 2 ans', '', [('2024-11-26', '2026-11-26')], ['depuis le 26 novembre 2024']),
    ('depuis Noël', '', [('2025-12-24', '2026-11-26')], ['depuis Noël 2025']),
    ('vers Noël', '', TOUS_LES_ANS, ['Noël (chaque année)']),
    # combinaisons
    ('Noël en 2019', '', [('2019-12-24', '2019-12-26')], ['Noël', '2019']),
    ('Noël chez mamie en 2019', 'chez mamie', [('2019-12-24', '2019-12-26')], ['Noël', '2019']),
    ('2019 Noël', '', [('2019-12-24', '2019-12-26')], ['2019', 'Noël']),
    ("Noël l'an dernier", '', [('2025-12-24', '2025-12-26')], ['Noël', '2025']),
    ('Noël cette année', '', [('2026-12-24', '2026-12-26')], ['Noël', '2026']),
    ('en août il y a 3 ans', '', [('2023-08-01', '2023-08-31')], ['août', 'vers 2023']),
    ('Noël il y a 2 ans', '', [('2024-12-24', '2024-12-26')], ['Noël', 'vers 2024']),
    ('Noël années 90', '', 10, ['Noël', 'années 1990']),
    ('Noël depuis 2019', '', 8, ['Noël', 'depuis 2019']),
    ('Noël avant 2000', '', 100, ['Noël', 'avant 2000']),
    ('Noël et Pâques en 2019', '', [('2019-04-20', '2019-04-22'), ('2019-12-24', '2019-12-26')],
     ['Noël', 'Pâques', '2019']),
    ("l'été en 2021", '', [('2021-06-01', '2021-09-22')], ['été', '2021']),
    ('en 2019 et en 2020', '', [('2019-01-01', '2019-12-31'), ('2020-01-01', '2020-12-31')], ['2019', '2020']),
    ('29 février en 2019', '', [('2019-01-01', '2019-12-31')], ['29 février', '2019']),
    ('Noël il y a quelques années', '', 7, ['Noël', '2018 à 2024']),
    # Relecture 0.5.0 : un nombre de deux chiffres qui commence un jour n'est pas une année (« Noël 25 décembre » :
    # Noël 2025 seulement ; « réveillon 31 décembre » : réveillons de 1931, aucune photo).
    ('Noël 25 décembre', '', 2 * TOUS_LES_ANS, ['Noël (chaque année)', '25 décembre (chaque année)']),
    ('réveillon 31 décembre', '', 3 * TOUS_LES_ANS, ['réveillons (chaque année)', '31 décembre (chaque année)']),
    ('Halloween 31 octobre', '', 2 * TOUS_LES_ANS, ['Halloween (chaque année)', '31 octobre (chaque année)']),
    ('Saint-Sylvestre 31 décembre', '', 2 * TOUS_LES_ANS,
     ['Saint-Sylvestre (chaque année)', '31 décembre (chaque année)']),
    ('Saint-Valentin 14 février', '', 2 * TOUS_LES_ANS, ['Saint-Valentin (chaque année)', '14 février (chaque année)']),
    ('fête de la musique 21 juin', '', 2 * TOUS_LES_ANS,
     ['fête de la musique (chaque année)', '21 juin (chaque année)']),
    ('Noël 24 au 26 décembre', '', TOUS_LES_ANS, ['Noël (chaque année)', '24 au 26 décembre (chaque année)']),
    ('Noël 10 ans', '10 ans', TOUS_LES_ANS, ['Noël (chaque année)']),
    ('Pâques 10 enfants', '10 enfants', TOUS_LES_ANS, ['Pâques (chaque année)']),
    ('Noël 12 personnes', '12 personnes', TOUS_LES_ANS, ['Noël (chaque année)']),
    # … mais « mai 68 », « été 98 », « mars 21 », « Noël 20 » restent des années, même suivies d'autres mots.
    ('Noël 20', '', [('2020-12-24', '2020-12-26')], ['Noël 2020']),
    ('mai 68 à Paris', 'Paris', [('1968-05-01', '1968-05-31')], ['mai 1968']),
    ('hiver 98 15 jours de ski', '15 jours de ski', 2, ['hiver 1998']),
]

# Pièges : ce qui doit rester des mots, ou rester des mots en partie : (texte, mots, nombre de périodes, libellés).
CAS_PIEGES = [
    ('a été', 'a été', 0, []),
    ('avait été', 'avait été', 0, []),
    ("j'ai été", "j'ai été", 0, []),
    ('il a été à la plage', 'il a été à la plage', 0, []),
    ('le chien a été lavé en 2019', 'le chien a été lavé', 1, ['2019']),
    ("le chien a été lavé l'été 2019", 'le chien a été lavé', 1, ['été 2019']),
    ('été', 'été', 0, []),
    ('Été', 'Été', 0, []),
    ("l'été indien", "l'été indien", 0, []),
    ("robe d'été", "robe d'été", 0, []),
    ("soirée d'été", "soirée d'été", 0, []),
    ("l'été prochain", "l'été prochain", 0, []),
    ('printemps', 'printemps', 0, []),
    ('Printemps Haussmann', 'Printemps Haussmann', 0, []),
    ('hiver', 'hiver', 0, []),
    ('neige hiver', 'neige hiver', 0, []),
    ('automne', 'automne', 0, []),
    ('mars', 'mars', 0, []),
    ('Mars', 'Mars', 0, []),
    ('planète Mars', 'planète Mars', 0, []),
    ('giboulées de mars', 'giboulées de mars', 0, []),
    ('Champ de Mars', 'Champ de Mars', 0, []),
    ('avril', 'avril', 0, []),
    ('Avril', 'Avril', 0, []),
    ('Avril en 2019', 'Avril', 1, ['2019']),
    ('mai', 'mai', 0, []),
    ('Mai', 'Mai', 0, []),
    ('il fait beau en mai', 'il fait beau', TOUS_LES_ANS, ['mai (chaque année)']),
    ('juillet', 'juillet', 0, []),
    ('Juillet', 'Juillet', 0, []),
    ('Juillet et Marie', 'Juillet et Marie', 0, []),
    ('août', 'août', 0, []),
    ('en sept', 'en sept', 0, []),
    ('sept chiens', 'sept chiens', 0, []),
    ('sept ans', 'sept ans', 0, []),
    ('2 chiens', '2 chiens', 0, []),
    ('3 ans', '3 ans', 0, []),
    ('enfant de 3 ans', 'enfant de 3 ans', 0, []),
    ('anniversaire 3 ans', 'anniversaire 3 ans', 0, []),
    ('100 ans', '100 ans', 0, []),
    ('anniversaire des 100 ans de mamie', 'anniversaire des 100 ans de mamie', 0, []),
    ('y a 2 ans', 'y a 2 ans', 0, []),
    ('il y a un chien', 'il y a un chien', 0, []),
    ('il y a 130 ans', 'il y a 130 ans', 0, []),
    ('12', '12', 0, []),
    ('1500', '1500', 0, []),
    ('1899', '1899', 0, []),
    ('2027', '2027', 0, []),
    ('2045', '2045', 0, []),
    ('en 2045', 'en 2045', 0, []),
    ('années 2030', 'années 2030', 0, []),
    ('1500 m', '1500 m', 0, []),
    ('2000 euros', '2000 euros', 0, []),
    ('2019 km', '2019 km', 0, []),
    ('n° 2019', 'n° 2019', 0, []),
    ('Peugeot 2008', 'Peugeot 2008', 0, []),
    ('Windows 2000', 'Windows 2000', 0, []),
    ('chambre 2019', 'chambre 2019', 0, []),
    ('le printemps de Bourges', 'le printemps de Bourges', 0, []),
    ("l'été de mes 10 ans", "l'été de mes 10 ans", 0, []),
    ('au printemps de la vie', 'au printemps de la vie', 0, []),
    ('14/07', '14/07', 0, []),
    ('10/10', '10/10', 0, []),
    ('3,5', '3,5', 0, []),
    ('1.5', '1.5', 0, []),
    ('31/02/2019', '31/02/2019', 0, []),
    ('29 février 2019', '29 février 2019', 0, []),
    ('31 avril', '31 avril', 0, []),
    ('Noël 2045', 'Noël 2045', 0, []),
    ('Pentecôte 2028', 'Pentecôte 2028', 0, []),
    ('mai 2045', 'mai 2045', 0, []),
    ('en mai 2045', 'en mai 2045', 0, []),
    ('été 2045', 'été 2045', 0, []),
    ('14 juillet 1789', '14 juillet 1789', 0, []),
    ('prise de la Bastille le 14 juillet 1789', 'prise de la Bastille le 14 juillet 1789', 0, []),
    ('30 février', '30 février', 0, []),
    ('pâques', '', TOUS_LES_ANS, ['Pâques (chaque année)']),
    ('œufs de Pâques', 'œufs de Pâques', 0, []),
    ('oeufs de paques', 'oeufs de paques', 0, []),
    ('chasse aux œufs de Pâques', 'chasse aux œufs de Pâques', 0, []),
    ('lapin de Pâques', 'lapin de Pâques', 0, []),
    ('île de Pâques', 'île de Pâques', 0, []),
    ('œufs de Pâques 2019', 'œufs de Pâques', 1, ['2019']),
    ('noël', '', TOUS_LES_ANS, ['Noël (chaque année)']),
    ('sapin de noël', 'sapin de noël', 0, []),
    ('sapin de Noël 2019', 'sapin de Noël', 1, ['2019']),
    ('marché de Noël', 'marché de Noël', 0, []),
    ('marché de Noël 2019', 'marché de Noël', 1, ['2019']),
    ("l'arbre de Noël", "l'arbre de Noël", 0, []),
    ('fête de Noël', 'fête de Noël', 0, []),
    ('père Noël', 'père Noël', 0, []),
    ('Père Noël 2019', 'Père Noël', 1, ['2019']),
    ('papa Noël', 'papa Noël', 0, []),
    ('Noël prochain', 'Noël prochain', 0, []),
    ('nouvel an chinois', 'nouvel an chinois', 0, []),
    ('ascension', 'ascension', 0, []),
    ('ASCENSION', 'ASCENSION', 0, []),
    ('ascension du mont Blanc', 'ascension du mont Blanc', 0, []),
    ("l'ascension du Mont Blanc", "l'ascension du Mont Blanc", 0, []),
    ('fête', 'fête', 0, []),
    ('fête des mères', 'fête des mères', 0, []),
    ('fête foraine', 'fête foraine', 0, []),
    ('anniversaire', 'anniversaire', 0, []),
    ("gâteau d'anniversaire", "gâteau d'anniversaire", 0, []),
    ('nuit', 'nuit', 0, []),
    ('nuit étoilée', 'nuit étoilée', 0, []),
    ('matin', 'matin', 0, []),
    ('soir', 'soir', 0, []),
    ('semaine', 'semaine', 0, []),
    ('mois', 'mois', 0, []),
    ('jour', 'jour', 0, []),
    ('demain', 'demain', 0, []),
    ('ce soir-là', 'ce soir-là', 0, []),
    ('ce soir là', 'ce soir là', 0, []),
    ('cette année-là', 'cette année-là', 0, []),
    ('cet été-là à la plage', 'cet été-là à la plage', 0, []),
    ('ce Noël-là', 'ce Noël-là', 0, []),
    ('ce mois-là', 'ce mois-là', 0, []),
    ('ce soir la plage', 'plage', 1, ['26 novembre 2026']),
    ('bonne année', 'bonne année', 0, []),
    ('vacances', 'vacances', 0, []),
    ('rue du 14 juillet', 'rue du 14 juillet', 0, []),
    ('avenue du 8 mai 1945', 'avenue du 8 mai 1945', 0, []),
    ('avant Noël', 'avant Noël', 0, []),
    ('après Pâques', 'après Pâques', 0, []),
    ('dimanche', 'dimanche', 0, []),
    ('photos', 'photos', 0, []),
    ('chien', 'chien', 0, []),
    ('chien noir', 'chien noir', 0, []),
    # Relecture 0.5.0 : deux chiffres devant une quantité (« été 30 degrés » donnait l'été 1930).
    ('été 30 degrés', 'été 30 degrés', 0, []),
    ('canicule été 35 degrés', 'canicule été 35 degrés', 0, []),
    ('été 30°', 'été 30°', 0, []),
    ('août 15 jours', 'août 15 jours', 0, []),
    ('juillet 10 jours en Corse', 'juillet 10 jours en Corse', 0, []),
    ('en août 15 jours', '15 jours', TOUS_LES_ANS, ['août (chaque année)']),
    # Relecture 0.5.0 : décennie d'un style, grandes dates de l'histoire (une commémoration, pas la photo).
    ('soirée années 80', 'soirée années 80', 0, []),
    ('déguisement années 80', 'déguisement années 80', 0, []),
    ('voiture des années 50', 'voiture des années 50', 0, []),
    ('robe des années 60', 'robe des années 60', 0, []),
    ('commémoration du 6 juin 1944', 'commémoration du 6 juin 1944', 0, []),
    ('cérémonie du 11 novembre 1918', 'cérémonie du 11 novembre 1918', 0, []),
    ('commémoration du 8 mai 1945', 'commémoration du 8 mai 1945', 0, []),
    ('monument aux morts 1914-1918', 'monument aux morts 1914-1918', 0, []),
    ('guerre 1939 à 1945', 'guerre 1939 à 1945', 0, []),
    ('photos des années 90', '', 1, ['années 1990']),
    ('vacances années 90', 'vacances', 1, ['années 1990']),
    ('6 juin 2019', '', 1, ['6 juin 2019']),
    # Relecture 0.5.0 : « selfie » est cherché par le moteur ; « hier soir » est une date entière, comme « ce soir ».
    ('mes selfies en 2019', 'mes selfies', 1, ['2019']),
    ('selfies de Noël 2019', 'selfies', 1, ['Noël 2019']),
    ('hier soir', '', 1, None),
    ('photos de hier soir', '', 1, None),
    ('chien hier matin', 'chien', 1, None),
    ('avant-hier soir', '', 1, None),
]


class TestDates(unittest.TestCase):
    """Dates à comprendre, le jeudi 26/11/2026 (un essai par cas)."""


class TestPieges(unittest.TestCase):
    """Anti-faux positifs : une fausse date ne doit jamais abîmer la recherche par mots."""


_generer(TestDates, CAS_DATES)
_generer(TestPieges, CAS_PIEGES)


class TestInterface(unittest.TestCase):

    def test_champs_des_classes(self):
        self.assertEqual([f.name for f in dataclasses.fields(Periode)], ['debut', 'fin', 'libelle'])
        self.assertEqual([f.name for f in dataclasses.fields(Analyse)], ['mots', 'periodes', 'lieux', 'puces'])
        a = analyser('chien en Bretagne à Noël 2019', AUJ, Lieux())
        self.assertIsInstance(a, Analyse)
        self.assertIsInstance(a.mots, str)
        self.assertTrue(all(isinstance(p, Periode) and type(p.debut) is date and type(p.fin) is date
                            for p in a.periodes))
        self.assertEqual([sorted(p) for p in a.puces], [['libelle', 'texte', 'type']] * 2)
        self.assertEqual([p['type'] for p in a.puces], ['lieu', 'date'])

    def test_recherche_vide(self):
        for texte in ('', None, '   ', '\t\n'):
            self.assertEqual(analyser(texte, AUJ, Lieux()), Analyse('', [], [], []))

    def test_sans_date_ni_lieu_le_texte_ne_change_pas(self):
        a = analyser('Chien  noir  !', AUJ, Lieux())
        self.assertEqual((a.mots, a.periodes, a.lieux, a.puces), ('Chien noir !', [], [], []))
        self.assertEqual(analyser('« gâteau » au chocolat', AUJ).mots, '« gâteau » au chocolat')

    def test_aujourd_hui_datetime_ou_date(self):
        self.assertEqual(analyser('hier', datetime(2026, 11, 26, 23, 59)), analyser('hier', AUJ))
        for faux in ('2026-11-26', None, 20261126, date(1, 1, 1)):
            with self.assertRaises(ValueError):
                analyser('hier', faux)

    def test_longueur_limitee(self):
        a = analyser('chien ' * 100 + 'Noël 2019', AUJ)
        self.assertEqual(a.periodes, [])                 # au-delà de 200 caractères : pas lu, comme la recherche
        self.assertLessEqual(len(a.mots), requete.LONGUEUR_MAX)

    def test_puces_dans_l_ordre_du_texte(self):
        a = analyser('en 2019 plage en Bretagne à Noël', AUJ, Lieux())
        self.assertEqual([(p['type'], p['texte']) for p in a.puces],
                         [('date', 'en 2019'), ('lieu', 'en Bretagne'), ('date', 'à Noël')])
        self.assertEqual(a.mots, 'plage')
        self.assertEqual(bornes(a), per(('2019-12-24', '2019-12-26')))

    def test_texte_de_la_puce_tel_que_tape(self):
        a = analyser("Chien sur la plage EN bretagne, L'ÉTÉ 2022 !", AUJ, Lieux())
        self.assertEqual([p['texte'] for p in a.puces], ['EN bretagne', "L'ÉTÉ 2022"])
        self.assertEqual(a.mots, 'Chien sur la plage')

    def test_periodes_triees_sans_doublon(self):
        a = analyser('2020 Noël 2019 2019', AUJ)
        self.assertEqual(bornes(a), sorted(set(bornes(a))))

    def test_l_analyse_ne_garde_rien(self):
        premiere = analyser('Noël chez mamie en 2019', AUJ)
        analyser('Noël', AUJ)
        self.assertEqual(analyser('Noël chez mamie en 2019', AUJ), premiere)


class TestPaques(unittest.TestCase):

    def test_dates_connues(self):
        connues = {1900: '1900-04-15', 1943: '1943-04-25', 2000: '2000-04-23', 2008: '2008-03-23',
                   2011: '2011-04-24', 2019: '2019-04-21', 2024: '2024-03-31', 2025: '2025-04-20',
                   2026: '2026-04-05', 2038: '2038-04-25', 2285: '2285-03-22'}
        for annee, jour in connues.items():
            self.assertEqual(paques(annee), D(jour), annee)

    def test_toujours_un_dimanche_entre_le_22_mars_et_le_25_avril(self):
        for annee in range(1900, 2101):
            p = paques(annee)
            self.assertEqual(p.weekday(), 6, annee)
            self.assertTrue(D(f'{annee}-03-22') <= p <= D(f'{annee}-04-25'), annee)

    def test_ascension_jeudi_pentecote_dimanche(self):
        plus_tard = D('2045-01-01')
        for annee in range(1990, 2040):
            a = analyser(f'Ascension {annee}', plus_tard).periodes[0]
            self.assertEqual((a.debut.weekday(), a.fin.weekday()), (3, 6))
            p = analyser(f'Pentecôte {annee}', plus_tard).periodes[0]
            self.assertEqual((p.debut.weekday(), p.fin.weekday()), (5, 0))
            self.assertEqual(p.debut + timedelta(days=1), paques(annee) + timedelta(days=49))


class TestSaisonsEtDernier(unittest.TestCase):
    """« cet été », « l'été dernier », « Noël dernier » selon le jour."""

    def test_l_ete_dernier_pendant_l_ete(self):
        self.assertEqual(bornes(analyser("l'été dernier", D('2026-07-15'))), per(('2025-06-01', '2025-09-22')))
        self.assertEqual(bornes(analyser('cet été', D('2026-07-15'))), per(('2026-06-01', '2026-09-22')))

    def test_l_ete_dernier_au_printemps_suivant(self):
        self.assertEqual(bornes(analyser("l'été dernier", D('2027-03-01'))), per(('2026-06-01', '2026-09-22')))
        self.assertEqual(bornes(analyser('cet été', D('2027-03-01'))), per(('2026-06-01', '2026-09-22')))

    def test_cet_hiver_a_cheval_sur_deux_annees(self):
        self.assertEqual(bornes(analyser('cet hiver', D('2027-01-10'))), per(('2026-12-01', '2027-03-20')))
        self.assertEqual(bornes(analyser('cet hiver', AUJ)), per(('2025-12-01', '2026-03-20')))
        self.assertEqual(bornes(analyser("l'hiver dernier", D('2027-01-10'))), per(('2025-12-01', '2026-03-20')))

    def test_noel_dernier_juste_apres_noel(self):
        self.assertEqual(bornes(analyser('Noël dernier', D('2026-12-27'))),
                         per(('2025-12-24', '2025-12-26'), ('2026-12-24', '2026-12-26')))
        self.assertEqual(bornes(analyser('Noël dernier', D('2026-12-25'))), per(('2025-12-24', '2025-12-26')))

    def test_aout_dernier(self):
        self.assertEqual(bornes(analyser('en août dernier', AUJ)),
                         per(('2025-08-01', '2025-08-31'), ('2026-08-01', '2026-08-31')))
        self.assertEqual(bornes(analyser('août dernier', D('2027-02-01'))), per(('2026-08-01', '2026-08-31')))

    def test_chaque_annee_de_1900_a_l_an_prochain(self):
        a = analyser('en août', AUJ)
        self.assertEqual((a.periodes[0].debut, a.periodes[-1].fin), (D('1900-08-01'), D('2027-08-31')))
        self.assertEqual(a.periodes[119].libelle, 'août 2019')
        h = analyser('en hiver', AUJ)
        self.assertEqual((h.periodes[0].debut, h.periodes[-1].fin), (D('1900-12-01'), D('2028-03-20')))


class TestRelatives(unittest.TestCase):
    """Dates relatives autour des semaines, des mois, des changements d'année et des années bissextiles."""

    def test_semaine_du_lundi_au_dimanche(self):
        for jour in ('2026-11-23', '2026-11-26', '2026-11-29'):          # lundi, jeudi, dimanche
            self.assertEqual(bornes(analyser('cette semaine', D(jour))), per(('2026-11-23', '2026-11-29')), jour)

    def test_semaine_derniere_au_changement_d_annee(self):
        self.assertEqual(bornes(analyser('la semaine dernière', D('2027-01-04'))),
                         per(('2026-12-28', '2027-01-03')))
        self.assertEqual(bornes(analyser('la semaine dernière', D('2027-01-01'))),
                         per(('2026-12-21', '2026-12-27')))

    def test_week_end(self):
        self.assertEqual(bornes(analyser('ce week-end', D('2026-11-28'))), per(('2026-11-28', '2026-11-29')))
        self.assertEqual(bornes(analyser('ce weekend', D('2026-11-29'))), per(('2026-11-28', '2026-11-29')))
        self.assertEqual(bornes(analyser('le week-end dernier', D('2026-11-29'))), per(('2026-11-21', '2026-11-22')))
        self.assertEqual(bornes(analyser('le week-end dernier', D('2026-11-30'))), per(('2026-11-28', '2026-11-29')))

    def test_hier_le_1er_janvier(self):
        self.assertEqual(bornes(analyser('hier', D('2027-01-01'))), per(('2026-12-31', '2026-12-31')))
        self.assertEqual(bornes(analyser('avant-hier', D('2024-03-01'))), per(('2024-02-28', '2024-02-28')))
        self.assertEqual(bornes(analyser('hier', D('2024-03-01'))), per(('2024-02-29', '2024-02-29')))

    def test_mois_dernier(self):
        self.assertEqual(bornes(analyser('le mois dernier', D('2027-01-10'))), per(('2026-12-01', '2026-12-31')))
        self.assertEqual(bornes(analyser('le mois dernier', D('2024-03-31'))), per(('2024-02-01', '2024-02-29')))
        self.assertEqual(bornes(analyser('ce mois-ci', D('2023-02-14'))), per(('2023-02-01', '2023-02-28')))

    def test_l_an_dernier_le_1er_janvier(self):
        self.assertEqual(bornes(analyser("l'an dernier", D('2027-01-01'))), per(('2026-01-01', '2026-12-31')))
        self.assertEqual(bornes(analyser('cette année', D('2027-01-01'))), per(('2027-01-01', '2027-12-31')))

    def test_il_y_a_un_mois_un_31_mars_bissextile(self):
        self.assertEqual(bornes(analyser('il y a 1 mois', D('2024-03-31'))), per(('2024-02-01', '2024-03-15')))
        self.assertEqual(bornes(analyser('il y a un mois', D('2023-03-31'))), per(('2023-02-01', '2023-03-15')))

    def test_il_y_a_un_an_un_29_fevrier(self):
        self.assertEqual(bornes(analyser('il y a un an', D('2024-02-29'))), per(('2022-11-28', '2023-12-31')))
        self.assertEqual(bornes(analyser('il y a 4 ans', D('2024-02-29'))), per(('2019-11-29', '2020-12-31')))

    def test_il_y_a_2_ans_en_janvier(self):
        a = analyser('il y a 2 ans', D('2027-01-10'))
        self.assertEqual(bornes(a), per(('2024-10-10', '2025-12-31')))  # le Noël 2024 est dedans
        self.assertEqual(bornes(analyser('Noël il y a 2 ans', D('2027-01-10'))), per(('2024-12-24', '2024-12-26')))

    def test_lundi_dernier_un_lundi(self):
        self.assertEqual(bornes(analyser('lundi dernier', D('2026-11-30'))), per(('2026-11-23', '2026-11-23')))
        self.assertEqual(bornes(analyser('dimanche dernier', D('2027-01-03'))), per(('2026-12-27', '2026-12-27')))

    def test_il_y_a_trop_loin(self):
        self.assertEqual(analyser('il y a 200 ans', AUJ).periodes, [])
        self.assertEqual(analyser('il y a 50000 jours', AUJ).periodes, [])

    def test_l_hiver_2020_un_jour_bissextile(self):
        h = analyser('hiver 2023', AUJ)
        self.assertTrue(any(p.debut <= D('2024-02-29') <= p.fin for p in h.periodes))


class TestCombinaisonsEtMots(unittest.TestCase):

    def test_petits_mots_laisses_seuls(self):
        cas = {'photos de Noël 2019': '', 'mes photos de 2019': '', 'toutes les photos en 2019': '',
               'chien de 2019 noir': 'chien noir', 'Noël et chien': 'chien', 'chien, Noël 2019': 'chien',
               'chien et 2019': 'chien', 'lors de la Toussaint': '', 'pendant les fêtes de Noël': '',
               'photo de famille en 2019': 'photo de famille', 'Noël à la montagne': 'montagne',
               '« chien » 2019': 'chien', "Gâteau d'Anniversaire en 2019": "Gâteau d'Anniversaire"}
        for texte, mots in cas.items():
            self.assertEqual(analyser(texte, AUJ).mots, mots, texte)

    def test_chaque_puce_garde_son_texte(self):
        a = analyser('photos de Noël 2019', AUJ)
        self.assertEqual(a.puces, [{'type': 'date', 'libelle': 'Noël 2019', 'texte': 'de Noël 2019'}])

    def test_le_libelle_chaque_annee_seulement_sans_annee(self):
        self.assertEqual([p['libelle'] for p in analyser('Noël', AUJ).puces], ['Noël (chaque année)'])
        self.assertEqual([p['libelle'] for p in analyser('Noël 2019 Pâques', AUJ).puces],
                         ['Noël 2019', 'Pâques (chaque année)'])


class TestIgnorer(unittest.TestCase):
    """× sur une puce : son texte redevient des mots."""

    def test_date_retiree(self):
        a = analyser('Noël 2019', AUJ, ignorer=['Noël 2019'])
        self.assertEqual((a.mots, a.periodes, a.puces), ('Noël 2019', [], []))

    def test_sans_accents_ni_majuscules(self):
        a = analyser('chien Noël 2019', AUJ, ignorer=['noel 2019'])
        self.assertEqual((a.mots, a.periodes), ('chien Noël 2019', []))

    def test_une_seule_puce_retiree(self):
        a = analyser('Noël chez mamie en 2019', AUJ, ignorer=('en 2019',))
        self.assertEqual(a.mots, 'chez mamie en 2019')
        self.assertEqual(len(a.periodes), TOUS_LES_ANS)
        self.assertEqual([p['texte'] for p in a.puces], ['Noël'])
        b = analyser('Noël chez mamie en 2019', AUJ, ignorer=['Noël'])
        self.assertEqual((b.mots, bornes(b)), ('Noël chez mamie', per(('2019-01-01', '2019-12-31'))))

    def test_lieu_retire(self):
        lieux = Lieux()
        a = analyser('plage en Bretagne', AUJ, lieux, ignorer=['en Bretagne'])
        self.assertEqual((a.mots, a.lieux, a.puces), ('plage en Bretagne', [], []))
        self.assertNotIn('bretagne', lieux.noms())

    def test_aller_retour_de_chaque_puce(self):
        texte = "chien à Paris en août 2019 et à Noël l'an dernier près de Saint-Malo"
        a = analyser(texte, AUJ, Lieux())
        self.assertEqual(len(a.puces), 5)
        for puce in a.puces:
            b = analyser(texte, AUJ, Lieux(), ignorer=[puce['texte']])
            self.assertNotIn(puce, b.puces)
            self.assertIn(puce['texte'], b.mots)
            self.assertEqual(len(b.puces), 4, puce)

    def test_texte_absent_ou_invalide(self):
        attendu = analyser('Noël 2019', AUJ)
        self.assertEqual(analyser('Noël 2019', AUJ, ignorer=['Pâques', '', None, 12, '   ']), attendu)
        self.assertEqual(analyser('Noël 2019', AUJ, ignorer='Noël 2019').periodes, [])   # une chaîne seule


class TestLieux(unittest.TestCase):
    """chercher_lieu factice : prépositions, majuscules, groupes de plusieurs mots, contractions."""

    def test_plage_en_bretagne(self):
        lieux = Lieux()
        a = analyser('plage en Bretagne', AUJ, lieux)
        self.assertEqual((a.mots, a.lieux), ('plage', [{'nom': 'Bretagne'}]))
        self.assertEqual(a.puces, [{'type': 'lieu', 'libelle': 'Bretagne', 'texte': 'en Bretagne'}])
        self.assertEqual(lieux.indices('bretagne'),
                         {'preposition': True, 'majuscule': True, 'mots': 1, 'debut': False, 'texte': 'Bretagne',
                          'article': False, 'prep': 'en', 'seul': False, 'complement': False, 'autres': []})

    def test_a_la_plage_n_est_pas_un_lieu(self):
        lieux = Lieux()
        a = analyser('à la plage', AUJ, lieux)
        self.assertEqual((a.mots, a.lieux, a.puces), ('à la plage', [], []))
        self.assertEqual(lieux.indices('la plage')['preposition'], True)
        self.assertEqual(lieux.indices('plage'), {'preposition': True, 'majuscule': False, 'mots': 1,
                                                  'debut': False, 'texte': 'plage', 'article': True, 'prep': 'a',
                                                  'seul': True, 'complement': False, 'autres': []})
        # « à la montagne », « à la plage » : l'article au début du nom compte aussi (relecture de la 0.5.0).
        self.assertTrue(lieux.indices('la plage')['article'])
        analyser('Plage', AUJ, lieux)
        self.assertEqual(lieux.appels[-1], ('plage', {'preposition': False, 'majuscule': True, 'mots': 1,
                                                      'debut': True, 'texte': 'Plage', 'article': False,
                                                      'prep': None, 'seul': True, 'complement': False,
                                                      'autres': []}))

    def test_article_entre_la_preposition_et_le_nom(self):
        for texte, nom, article in (('chien à la plage', 'plage', True), ('bateau au port', 'port', True),
                                    ("lever à l'aube", 'aube', True), ('photos à Orange', 'orange', False),
                                    ('aux Sables-d’Olonne', 'sables d olonne', True), ('en Vienne', 'vienne', False),
                                    ('près du Mans', 'mans', True), ('vers Lyon', 'lyon', False)):
            lieux = Lieux()
            analyser(texte, AUJ, lieux)
            self.assertEqual(lieux.indices(nom)['article'], article, texte)
            self.assertTrue(lieux.indices(nom)['preposition'], texte)

    def test_prepositions(self):
        cas = {'à la Baule': 'La Baule', 'dans le Finistère': 'Finistère', 'près de Nantes': 'Nantes',
               "près d'Aix-en-Provence": 'Aix-en-Provence', 'du côté de Brest': 'Brest', 'vers Lyon': 'Lyon',
               'à côté de Lyon': 'Lyon', 'autour de Brest': 'Brest', 'aux Sables-d’Olonne': "Les Sables-d'Olonne",
               'au Mans': 'Le Mans', 'près du Mans': 'Le Mans', 'a Paris': 'Paris'}
        for texte, nom in cas.items():
            a = analyser('chien ' + texte, AUJ, Lieux())
            self.assertEqual((a.mots, [p['libelle'] for p in a.puces], a.puces[0]['texte']),
                             ('chien', [nom], texte), texte)

    def test_contraction_essayee_apres_le_nom_seul(self):
        lieux = Lieux()
        analyser('au Mans', AUJ, lieux)
        self.assertEqual(lieux.noms(), ['mans', 'le mans'])

    def test_groupes_de_plusieurs_mots(self):
        for texte, nom, mots in (('Saint-Malo', 'saint malo', 2), ('St-Malo', 'saint malo', 2),
                                 ('Aix-en-Provence', 'aix en provence', 3),
                                 ('Saintes-Maries-de-la-Mer', 'saintes maries de la mer', 5),
                                 ('Saint-Jean-Pied-de-Port', 'saint jean pied de port', 5)):
            lieux = Lieux()
            a = analyser('coucher de soleil à ' + texte, AUJ, lieux)
            self.assertEqual(a.mots, 'coucher de soleil', texte)
            self.assertEqual(lieux.indices(nom)['mots'], mots)

    def test_le_plus_long_d_abord(self):
        lieux = Lieux()
        analyser('Saint Malo', AUJ, lieux)
        self.assertEqual(lieux.noms(), ['saint malo'])

    def test_majuscule_et_debut(self):
        lieux = Lieux()
        self.assertEqual(analyser('Nice', AUJ, lieux).lieux, [{'nom': 'Nice'}])
        self.assertEqual(lieux.indices('nice'), {'preposition': False, 'majuscule': True, 'mots': 1, 'debut': True,
                                                 'texte': 'Nice', 'article': False, 'prep': None, 'seul': True,
                                                 'complement': False, 'autres': []})
        lieux = Lieux()
        analyser('Nice et Cannes', AUJ, lieux)                   # pas toute la recherche : seul=False
        self.assertEqual((lieux.indices('nice')['debut'], lieux.indices('nice')['seul']), (True, False))
        self.assertEqual(analyser('une nice photo', AUJ, Lieux()).lieux, [])
        self.assertEqual(analyser('à nice', AUJ, Lieux()).lieux, [{'nom': 'Nice'}])
        lieux = Lieux()
        analyser('plage la Baule', AUJ, lieux)
        self.assertTrue(lieux.indices('la baule')['majuscule'])

    def test_tout_en_capitales(self):
        lieux = Lieux()
        a = analyser('PLAGE À NICE', AUJ, lieux)
        self.assertEqual((a.mots, a.lieux), ('PLAGE', [{'nom': 'Nice'}]))
        self.assertFalse(lieux.indices('nice')['majuscule'])
        self.assertFalse(lieux.indices('plage')['majuscule'])

    def test_jamais_sur_une_date_ni_un_petit_mot(self):
        lieux = Lieux()
        a = analyser('Noël à Paris en 2019 avec Avril', AUJ, lieux)
        self.assertEqual((a.mots, a.lieux), ('avec Avril', [{'nom': 'Paris'}]))
        for nom in lieux.noms():
            self.assertFalse(set(nom.split()) & {'noel', '2019', 'en', 'a'}, nom)
        self.assertNotIn('avril', lieux.noms())                          # un mois seul n'est jamais un lieu
        self.assertNotIn('avec', lieux.noms())

    def test_pas_a_travers_une_virgule(self):
        lieux = Lieux()
        a = analyser('Paris, Texas', AUJ, lieux)
        self.assertNotIn('paris texas', lieux.noms())
        self.assertEqual(a.lieux, [{'nom': 'Paris'}, {'nom': 'Texas'}])

    def test_nom_qui_contient_une_fete_ou_un_mois(self):
        a = analyser('île de Pâques 2019', AUJ, Lieux())
        self.assertEqual((a.mots, a.lieux, bornes(a)), ('', [{'nom': 'Île de Pâques'}],
                                                         per(('2019-01-01', '2019-12-31'))))
        b = analyser('tour Eiffel depuis le Champ de Mars en mars 2021', AUJ, Lieux())
        self.assertEqual((b.mots, b.lieux, bornes(b)), ('tour Eiffel', [{'nom': 'Champ de Mars'}],
                                                         per(('2021-03-01', '2021-03-31'))))

    def test_plusieurs_lieux_dates_et_mots(self):
        a = analyser('chien à Paris et à Lyon en 2019', AUJ, Lieux())
        self.assertEqual((a.mots, a.lieux), ('chien', [{'nom': 'Paris'}, {'nom': 'Lyon'}]))
        self.assertEqual([p['type'] for p in a.puces], ['lieu', 'lieu', 'date'])

    def test_sans_chercher_lieu(self):
        a = analyser('plage en Bretagne', AUJ)
        self.assertEqual((a.mots, a.lieux, a.puces), ('plage en Bretagne', [], []))

    def test_photos_de_paris(self):
        self.assertEqual(analyser('photos de Paris', AUJ, Lieux()).mots, '')

    def test_reponses_vides_et_erreurs(self):
        for reponse in ([], '', {}, (), None):
            a = analyser('plage en Bretagne', AUJ, lambda n, i, r=reponse: r)
            self.assertEqual((a.mots, a.lieux), ('plage en Bretagne', []), repr(reponse))

        def panne(nom, indices):
            raise RuntimeError('lieux en panne (essai)')
        requete._STRICT = False
        try:
            a = analyser('plage en Bretagne en 2019', AUJ, panne)
        finally:
            requete._STRICT = True
        self.assertEqual((a.mots, a.lieux, len(a.periodes)), ('plage en Bretagne', [], 1))

    def test_libelle_de_la_puce(self):
        class Endroit:
            def __init__(self, nom):
                self.nom = nom
        cas = [({'libelle': 'Brest, Finistère', 'nom': 'Brest'}, 'Brest, Finistère'), (Endroit('Brest'), 'Brest'),
               ([Endroit('Brest'), Endroit('Brest (Biélorussie)')], 'Brest'), ('Brest', 'Brest'),
               (object(), 'brest'), (42, 'brest')]
        for objet, libelle in cas:
            a = analyser('à brest', AUJ, lambda n, i, o=objet: o if n == 'brest' else None)
            self.assertEqual(a.puces, [{'type': 'lieu', 'libelle': libelle, 'texte': 'à brest'}], repr(objet))
            self.assertIs(a.lieux[0], objet)

    def test_indices_est_une_copie(self):
        vus = []

        def garde(nom, indices):
            indices['preposition'] = 'abîmé'
            vus.append(indices)
        analyser('à la plage de Bretagne', AUJ, garde)
        self.assertTrue(all(len(v) == 10 for v in vus))


class TestNormalisation(unittest.TestCase):

    def test_exemples(self):
        cas = {'Saint-Malo': 'saint malo', 'St Malo': 'saint malo', 'ST-MALO': 'saint malo',
               "L'Haÿ-les-Roses": 'l hay les roses', 'Œuf  de  Pâques': 'oeuf de paques', "  Île-d'Yeu ": 'ile d yeu',
               'Sainte-Anne': 'sainte anne', 'Ste Anne': 'sainte anne', 'Łódź': 'lodz', 'Ærø': 'aero',
               'São Paulo': 'sao paulo', 'Aix–en–Provence': 'aix en provence'}
        for texte, attendu in cas.items():
            self.assertEqual(normaliser(texte), attendu, texte)

    def test_meme_regle_que_lieux_py(self):
        try:
            from myphotos import lieux
        except ImportError:
            self.skipTest("myphotos/lieux.py n'existe pas encore : la copie de requete.py sert")
        for texte in ('Saint-Malo', "L'Haÿ-les-Roses", 'Œuf', 'São Paulo', 'Ste-Anne', 'Łódź', 'x_y', 'ﬁn', 'Ⅻ'):
            self.assertEqual(normaliser(texte), lieux.normaliser(texte), texte)
        self.assertEqual(requete._nom_lieu('St-Malo'), 'saint malo')
        self.assertIs(requete._NORMALISER_LIEU[0], lieux.normaliser)         # celle de lieux.py fait foi


class TestFiltreDates(unittest.TestCase):

    def test_intervalles_fusionnes(self):
        p = [Periode(D('2019-12-24'), D('2019-12-26'), 'a'), Periode(D('2019-12-20'), D('2019-12-24'), 'b'),
             Periode(D('2019-12-27'), D('2019-12-28'), 'c'), Periode(D('2020-01-05'), D('2020-01-05'), 'd')]
        self.assertEqual(intervalles(p), per(('2019-12-20', '2019-12-28'), ('2020-01-05', '2020-01-05')))
        self.assertEqual(intervalles([]), [])

    def test_contient(self):
        p = analyser('Noël', AUJ).periodes
        self.assertTrue(contient(p, D('2019-12-25')))
        self.assertTrue(contient(p, datetime(2003, 12, 26, 23, 59)))
        self.assertTrue(contient(p, '2010-12-24T08:00:00'))
        self.assertFalse(contient(p, '2010-12-27T00:00:00'))
        self.assertFalse(contient(p, D('1899-12-25')))
        for rien in (None, '', 'pas une date', 42):
            self.assertFalse(contient(p, rien), rien)

    def test_sans_periode_tout_passe(self):
        f = FiltreDates([])
        self.assertFalse(f)
        self.assertIn(None, f)
        self.assertEqual(f.sql('d'), ('1', []))

    def test_masque_numpy(self):
        import numpy as np
        f = FiltreDates(analyser('hiver 2020', AUJ).periodes)
        dates = np.array(['2019-11-30T23:59:59', '2019-12-01T00:00:00', '2020-03-20T23:00:00', '2020-06-01T12:00:00',
                          '2021-01-01T00:00:00', 'NaT'], dtype='datetime64[s]')
        self.assertEqual(f.masque(dates).tolist(), [False, True, True, False, True, False])
        self.assertEqual(FiltreDates([]).masque(dates).tolist(), [True] * 6)
        self.assertEqual(f.masque(np.array([], dtype='datetime64[s]')).tolist(), [])

    def test_masque_rapide_et_juste_sur_100000_dates(self):
        import numpy as np
        rng = np.random.default_rng(7)
        secondes = rng.integers(0, 4_000_000_000, 100_000) - 2_000_000_000       # 1906 à 2033
        dates = secondes.astype('datetime64[s]')
        f = FiltreDates(analyser('Noël', AUJ).periodes)
        t0 = time.perf_counter()
        m = f.masque(dates)
        duree = time.perf_counter() - t0
        attendu = [d.astype(object) in f for d in dates[:2000]]
        self.assertEqual(m[:2000].tolist(), attendu)
        self.assertLess(duree, 0.5)

    def test_sql(self):
        f = FiltreDates(analyser('Noël 2019 et 2020', AUJ).periodes)
        clause, params = f.sql('COALESCE(date_prise, date_fichier)')
        self.assertEqual(params, ['2019-12-24', '2019-12-27', '2020-12-24', '2020-12-27'])
        c = sqlite3.connect(':memory:')
        c.execute('CREATE TABLE photos (id INTEGER, date_prise TEXT, date_fichier TEXT)')
        c.executemany('INSERT INTO photos VALUES (?, ?, ?)', [
            (1, '2019-12-24T00:00:00', None), (2, None, '2019-12-26T23:59:59'), (3, '2019-12-27T00:00:00', None),
            (4, '2020-12-25T10:00:00', '2001-01-01T00:00:00'), (5, None, None), (6, '2019-12-23T23:59:59', None)])
        ids = [i for (i,) in c.execute(f'SELECT id FROM photos WHERE {clause} ORDER BY id', params)]
        c.close()
        self.assertEqual(ids, [1, 2, 4])


class TestRobustesse(unittest.TestCase):

    def test_textes_etranges(self):
        for texte in ('🎄 Noël 2019 🎅', '٢٠١٩', 'Noëĺ 2019', '99999999999999999999', '0000', '00/00/0000',
                      '9999-12-31', '2019-02-30', '1/1/1', '-', '– –', "''", 'l\'', "aujourd'", 'il y a',
                      'il y a 0 jours', 'il y a -2 ans', 'depuis', 'avant', 'entre 2019 et', 'de mars à',
                      'du 3 au', 'années', 'fin des années', 'hiver 2019-2021', 'Noël 2019-2018', '14 juillet 1789',
                      'en 1999 1999 1999', 'x' * 500, 'é' * 300, '\x00\x01', 'Noël' * 60):
            a = analyser(texte, AUJ, Lieux())
            self.assertIsInstance(a.mots, str)
            for p in a.periodes:
                self.assertLessEqual(p.debut, p.fin)

    def test_au_hasard(self):
        """2 000 recherches tirées au hasard dans le vocabulaire des dates : jamais d'erreur, des périodes en
        ordre, des puces qui sont du texte tapé."""
        mots = ('Noël', 'noel', 'Pâques', 'été', "l'été", 'hiver', 'mars', 'en', 'à', 'au', 'de', 'du', 'le', 'la',
                "l'", 'les', '2019', '2020', '98', '21', '1500', '2045', '14', '1er', '3', 'juillet', 'août', 'mai',
                'il', 'y', 'a', 'ans', 'mois', 'semaines', 'jours', 'dernier', 'prochain', 'cet', 'ce', 'cette',
                'semaine', 'année', 'années', '90', 'depuis', 'avant', 'après', 'vers', 'entre', 'et', 'ou', 'fin',
                'début', 'mi', 'chien', 'plage', 'Bretagne', 'Paris', 'Saint', 'Malo', 'Champ', 'Mars', '/', '-',
                ',', '2018-2020', '14/07/2019', '2019-07-14', 'Saint-Sylvestre', 'Nouvel', 'An', 'réveillon',
                'vacances', 'lundi', 'hier', "aujourd'hui", 'quelques', 'deux', 'sapin', 'père', 'photos', 'rue')
        rng = random.Random(2026)
        for _ in range(2000):
            texte = ' '.join(rng.choice(mots) for _ in range(rng.randint(1, 9)))
            for auj in (AUJ, D('2024-02-29'), D('2027-01-01')):
                a = analyser(texte, auj, Lieux())
                for p in a.periodes:
                    self.assertLessEqual(p.debut, p.fin, texte)
                    self.assertGreaterEqual(p.debut.year, 1899, texte)
                for puce in a.puces:
                    self.assertIn(puce['texte'], texte)
                    self.assertTrue(puce['libelle'], texte)
                self.assertEqual(bool(a.puces), bool(a.periodes or a.lieux), texte)


class TestPerformance(unittest.TestCase):

    def test_moins_d_une_milliseconde_par_recherche_typique(self):
        requetes = ["chien sur la plage en Bretagne l'été 2022", 'Noël 2019', 'chien', "gâteau d'anniversaire",
                    'Noël chez mamie en 2019', 'mariage 2015', 'coucher de soleil à Saint-Malo', 'il y a 2 ans',
                    'vacances en août', 'Noël', 'photos de Paris', 'la semaine dernière', 'neige hiver 2020',
                    'chat noir sur le canapé', 'anniversaire de mamie 2018']
        lieux = Lieux()
        for q in requetes:
            analyser(q, AUJ, lieux)
        durees = []
        for _ in range(30):
            for q in requetes:
                t0 = time.perf_counter()
                analyser(q, AUJ, lieux)
                durees.append(time.perf_counter() - t0)
        moyenne, mediane = statistics.fmean(durees), statistics.median(durees)
        print(f'\n  analyse : moyenne {moyenne * 1e3:.3f} ms, médiane {mediane * 1e3:.3f} ms, '
              f'pire {max(durees) * 1e3:.3f} ms', file=sys.stderr)
        self.assertLess(mediane, 0.001)
        self.assertLess(moyenne, 0.001)


if __name__ == '__main__':
    unittest.main()
