"""Tests du programme de l'automate (sans réseau).

Lancement :  uv run pytest
"""

from automate import Regulateur, Tempo
from mapping import ETAPE_ATTENTE, ETAPE_DEFAUT, ETAPE_MANOEUVRE, ETAPE_TEMPORISATION

CYCLE_MS = 100
TENSION_BASSE = 20.0        # sous la bande 20,295 - 20,705 kV (consigne 20,5 ± 1 %)
TENSION_HAUTE = 21.0
TENSION_OK = 20.5


def faire_tourner(reg, secondes, u_hta=TENSION_OK, prise=9, manoeuvre=False,
                  auto=True, plus=False, moins=False, acquit=False):
    """Fait tourner le régulateur pendant un certain temps avec des entrées fixes."""
    for _ in range(round(secondes * 1000 / CYCLE_MS)):
        reg.cycle(u_hta, prise, manoeuvre, auto, plus, moins, acquit, CYCLE_MS)


def test_tempo_comme_un_ton():
    t = Tempo(1000)
    for _ in range(9):
        assert not t.cycle(True, 100)
    assert t.cycle(True, 100)           # 1000 ms atteintes
    assert not t.cycle(False, 100)      # entrée retombée : remise à zéro
    assert t.ecoule_ms == 0


def test_dans_la_bande_aucun_ordre():
    reg = Regulateur()
    faire_tourner(reg, 60)
    assert reg.etape == ETAPE_ATTENTE
    assert not reg.ordre_monter and not reg.ordre_descendre


def test_tension_basse_monter_apres_tempo_1():
    reg = Regulateur()
    faire_tourner(reg, 9.5, u_hta=TENSION_BASSE)
    assert reg.etape == ETAPE_TEMPORISATION and not reg.ordre_monter
    assert reg.temps_restant() == 1
    faire_tourner(reg, 1, u_hta=TENSION_BASSE)
    assert reg.ordre_monter and not reg.ordre_descendre


def test_tension_haute_descendre():
    reg = Regulateur()
    faire_tourner(reg, 11, u_hta=TENSION_HAUTE)
    assert reg.ordre_descendre and not reg.ordre_monter


def test_retour_dans_la_bande_annule_la_tempo():
    reg = Regulateur()
    faire_tourner(reg, 8, u_hta=TENSION_BASSE)
    faire_tourner(reg, 0.1, u_hta=TENSION_OK)
    assert reg.etape == ETAPE_ATTENTE
    faire_tourner(reg, 8, u_hta=TENSION_BASSE)     # la tempo repart de zéro
    assert not reg.ordre_monter


def test_manoeuvre_complete_puis_tempo_2():
    reg = Regulateur()
    faire_tourner(reg, 11, u_hta=TENSION_BASSE)
    assert reg.ordre_monter
    # Le régleur répond : l'ordre retombe
    faire_tourner(reg, 5, u_hta=TENSION_BASSE, manoeuvre=True)
    assert reg.etape == ETAPE_MANOEUVRE and not reg.ordre_monter
    # Fin de manœuvre, tension toujours basse : 2e manœuvre avec la tempo 2 (5 s)
    faire_tourner(reg, 5.5, u_hta=TENSION_BASSE, prise=10)
    assert reg.nb_manoeuvres == 1
    assert reg.ordre_monter


def test_butees():
    reg = Regulateur()
    faire_tourner(reg, 60, u_hta=TENSION_BASSE, prise=17)
    assert not reg.ordre_monter and reg.butee
    faire_tourner(reg, 60, u_hta=TENSION_HAUTE, prise=1)
    assert not reg.ordre_descendre and reg.butee


def test_blocage_sous_tension():
    reg = Regulateur()
    faire_tourner(reg, 60, u_hta=10.0)
    assert reg.blocage
    assert reg.etape == ETAPE_ATTENTE and not reg.ordre_monter


def test_mode_manuel():
    reg = Regulateur()
    faire_tourner(reg, 60, u_hta=TENSION_BASSE, auto=False)
    assert not reg.ordre_monter                     # pas de régulation en manuel
    faire_tourner(reg, 0.1, auto=False, plus=True)
    assert reg.ordre_monter                         # ordre immédiat sur le bouton +
    reg2 = Regulateur()
    faire_tourner(reg2, 0.1, prise=1, auto=False, moins=True)
    assert not reg2.ordre_descendre                 # butée basse respectée


def test_defaut_regleur_puis_acquittement():
    reg = Regulateur()
    faire_tourner(reg, 11, u_hta=TENSION_BASSE)
    faire_tourner(reg, 10.5, u_hta=TENSION_BASSE, manoeuvre=True)  # manœuvre trop longue
    assert reg.etape == ETAPE_DEFAUT and reg.defaut
    assert not reg.mode_auto and not reg.ordre_monter
    faire_tourner(reg, 1, u_hta=TENSION_BASSE, auto=False, plus=True)
    assert not reg.ordre_monter                     # ordres interdits pendant le défaut
    faire_tourner(reg, 0.1, auto=False, manoeuvre=True, acquit=True)
    assert reg.defaut                               # refusé : manœuvre encore en cours
    faire_tourner(reg, 0.1, auto=False, acquit=True)
    assert reg.etape == ETAPE_ATTENTE and not reg.defaut


def test_reglages_hors_plage_bornes():
    reg = Regulateur()
    reg.regler(30.0, 0.1, 0, 500)
    assert (reg.consigne, reg.bande_morte, reg.tempo_1, reg.tempo_2) == (22.0, 0.7, 1, 120)
