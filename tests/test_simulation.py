"""Tests du modèle de simulation (sans réseau).

Lancement :  uv run pytest
"""

import random

from automate import PRISE_MAX as BUTEE_HAUTE_AUTOMATE
from automate import Regulateur
from simulation import PRISE_MAX, PRISE_MIN, Regleur, VieReelle, profil_journalier, tension_hta


def test_tension_hta_prise_neutre():
    assert tension_hta(63, 9, 0) == 20.0            # 63 kV, prise neutre, sans charge


def test_tension_hta_prises_extremes():
    assert round(tension_hta(63, 17, 0), 3) == 22.0     # + 10 %
    assert round(tension_hta(63, 1, 0), 3) == 18.0      # - 10 %


def test_tension_hta_chute_en_charge():
    assert round(tension_hta(63, 9, 1000), 3) == 19.0   # 1 kV perdu à 1000 A
    assert tension_hta(60, 9, 0) < tension_hta(63, 9, 0)


def test_tension_hta_jamais_negative():
    assert tension_hta(0, 9, 800) == 0.0                # perte de la HTB : pas de tension négative


def test_regleur_au_repos():
    r = Regleur()
    r.cycle(False, False, 1)
    assert r.prise == 9 and not r.en_manoeuvre


def test_regleur_monte_en_5_secondes():
    r = Regleur()
    r.cycle(True, False, 0.5)
    assert r.en_manoeuvre and r.prise == 9              # commence, mais la prise n'a pas bougé
    for _ in range(9):
        r.cycle(False, False, 0.5)                      # 4,5 s
    assert r.en_manoeuvre and r.prise == 9
    r.cycle(False, False, 0.5)                          # 5,0 s : fin de la manoeuvre
    assert not r.en_manoeuvre and r.prise == 10


def test_regleur_prise_visee():
    r = Regleur()
    assert r.prise_visee() == 0                         # au repos
    r.cycle(True, False, 0.1)
    assert r.prise_visee() == 10                        # en route de 9 vers 10
    r.cycle(False, False, 5)
    assert r.prise_visee() == 0 and r.prise == 10


def test_regleur_descend():
    r = Regleur()
    r.cycle(False, True, 1)
    r.cycle(False, False, 5)
    assert r.prise == 8


def test_regleur_ignore_un_ordre_pendant_la_manoeuvre():
    r = Regleur()
    r.cycle(True, False, 1)
    r.cycle(False, True, 1)                             # ordre contraire : ignoré
    r.cycle(False, False, 5)
    assert r.prise == 10


def test_regleur_butees():
    r = Regleur()
    r.prise = PRISE_MAX
    r.cycle(True, False, 1)
    assert not r.en_manoeuvre and r.prise == PRISE_MAX
    r.prise = PRISE_MIN
    r.cycle(False, True, 1)
    assert not r.en_manoeuvre and r.prise == PRISE_MIN


def test_vie_reelle_valeurs_realistes():
    random.seed(1)
    vie = VieReelle()
    for _ in range(50000):                              # environ 3 ans simulés
        vie.cycle(0.1)
        assert 100 <= vie.i_charge <= 1100
        assert 63 * 0.95 <= vie.u_htb <= 63 * 1.05      # HTB à ± 5 %
        assert -10 < vie.temperature < 35


def test_vie_reelle_le_calendrier_avance():
    vie = VieReelle(heure=6, jour=15)
    for _ in range(2400):                               # 240 s : une journée simulée
        vie.cycle(0.1)
    assert round(vie.heure) == 6
    assert vie.jour == 30                               # + 15 jours


def test_vie_reelle_hiver_plus_charge_que_ete():
    random.seed(1)
    hiver = VieReelle(heure=18, jour=15)
    ete = VieReelle(heure=18, jour=196)
    assert hiver.i_charge > ete.i_charge + 300
    assert hiver.temperature < ete.temperature


def test_profil_journalier():
    assert profil_journalier(19) == 1.0                 # pointe du soir
    assert profil_journalier(4) < profil_journalier(9) < profil_journalier(19)   # creux, matin, soir
    assert profil_journalier(5.5) == (0.48 + 0.58) / 2  # entre 5 h et 6 h : interpolation
    assert profil_journalier(23.5) == (0.62 + 0.55) / 2 # de 23 h à minuit : on reboucle


def test_vie_reelle_pointe_du_soir_et_creux_de_nuit():
    random.seed(1)
    soir = VieReelle(heure=19, jour=15)
    nuit = VieReelle(heure=4, jour=15)
    assert soir.i_charge > nuit.i_charge + 250


def test_boucle_fermee_regulation_sur_deux_journees():
    """Automate + régleur + charge : la tension reste près de la consigne le 2e jour."""
    random.seed(2)
    vie = VieReelle(heure=6, jour=15)
    regleur = Regleur()
    regul = Regulateur()
    dt_s, dt_ms = 0.1, 100
    ordre_monter = ordre_descendre = False
    hors_bande = 0
    for n in range(4800):                               # 480 s : deux journées simulées
        vie.cycle(dt_s)
        regleur.cycle(ordre_monter, ordre_descendre, dt_s)
        u = tension_hta(vie.u_htb, regleur.prise, vie.i_charge)
        regul.cycle(u, regleur.prise, regleur.en_manoeuvre, True, False, False, False, dt_ms)
        ordre_monter, ordre_descendre = regul.ordre_monter, regul.ordre_descendre
        assert PRISE_MIN <= regleur.prise <= BUTEE_HAUTE_AUTOMATE
        assert not regul.defaut
        # Le 2e jour, l'écart reste sous 3 % : bande morte (1 %) + retard de la tempo et de la
        # manœuvre quand la charge monte vite (pointe du matin)
        if n > 2400 and abs(u - 20.5) > 20.5 * 0.03:
            hors_bande += 1
    assert hors_bande == 0
    assert regleur.prise > 9                            # il a fallu monter pour compenser la chute
