"""Simulation du poste : réseau HTB, charge, transformateur et régleur en charge.

Partie 1 (ce qui suit) : le MODÈLE, sans aucun réseau. On peut le tester seul
    avec pytest (tests/test_simulation.py).
    - tension_hta : la formule du transformateur ;
    - Regleur     : le régleur en charge (une manœuvre dure 5 s) ;
    - VieReelle   : U_HTB, charge et température qui varient comme dans la vraie vie.

Partie 2 (étape 4) : client Modbus qui fait tourner le modèle.

Le temps est compté en secondes (dt_s = durée du cycle).
"""

import math
import random

PRISE_MIN = 1
PRISE_NEUTRE = 9
PRISE_MAX = 17
PAS_PRISE = 0.0125          # 1,25 % par prise
DUREE_MANOEUVRE_S = 5       # durée d'un changement de prise
K_CHUTE = 0.001             # kV perdus par ampère de charge
U_HTB_NOMINALE = 63.0       # kV
U_HTA_NOMINALE = 20.0       # kV
HEURES_PAR_SECONDE = 0.1    # 1 s réelle = 6 min simulées : une journée dure 4 min
JOURS_PAR_JOUR = 15         # le calendrier avance de 15 jours par journée simulée


def tension_hta(u_htb, prise, i_charge):
    """Tension du jeu de barres HTA (kV) : rapport de transformation moins chute en charge."""
    rapport = U_HTA_NOMINALE * (u_htb / U_HTB_NOMINALE)
    return rapport * (1 + PAS_PRISE * (prise - PRISE_NEUTRE)) - K_CHUTE * i_charge


class Regleur:
    """Régleur en charge : reçoit un ordre, met 5 s à changer de prise."""

    def __init__(self):
        self.prise = PRISE_NEUTRE
        self.en_manoeuvre = False
        self.sens = 0               # +1 = monter, -1 = descendre
        self.chrono = 0.0           # temps écoulé depuis le début de la manœuvre

    def cycle(self, ordre_monter, ordre_descendre, dt_s):
        if not self.en_manoeuvre:
            # Un nouvel ordre n'est pris que si le régleur est au repos et pas en butée
            if ordre_monter and self.prise < PRISE_MAX:
                self.sens = +1
            elif ordre_descendre and self.prise > PRISE_MIN:
                self.sens = -1
            else:
                return
            self.en_manoeuvre = True
            self.chrono = 0.0
        else:
            self.chrono += dt_s
            if self.chrono >= DUREE_MANOEUVRE_S:    # fin : la prise change d'un coup
                self.prise += self.sens
                self.en_manoeuvre = False


class VieReelle:
    """Fait varier U_HTB, la charge et la température comme dans la vraie vie.

    Deux cycles se superposent : le jour (creux de charge la nuit, pointe vers
    18 h) et l'année (charge forte en hiver, faible en été). On y ajoute un
    petit aléa lent (marche aléatoire) pour que deux journées ne soient pas identiques.
    """

    def __init__(self, heure=6.0, jour=15):
        self.heure = heure          # heure simulée (0 à 24)
        self.jour = jour            # jour de l'année (1 à 365)
        self.alea_htb = 0.0         # entre -1 et +1
        self.alea_charge = 0.0
        self.u_htb = U_HTB_NOMINALE
        self.i_charge = 500.0
        self.temperature = 10.0
        self.cycle(0)

    def cycle(self, dt_s):
        # Le temps avance : une journée de plus tous les 24 h simulées
        self.heure += dt_s * HEURES_PAR_SECONDE
        if self.heure >= 24:
            self.heure -= 24
            self.jour = (self.jour - 1 + JOURS_PAR_JOUR) % 365 + 1
        # L'aléa se promène lentement entre -1 et +1
        self.alea_htb = max(-1, min(1, self.alea_htb + random.uniform(-1, 1) * 0.05 * dt_s))
        self.alea_charge = max(-1, min(1, self.alea_charge + random.uniform(-1, 1) * 0.05 * dt_s))
        # Cycles du jour et de l'année : +1 le soir vers 18 h / mi-janvier
        soir = math.cos(2 * math.pi * (self.heure - 18) / 24)
        hiver = math.cos(2 * math.pi * (self.jour - 15) / 365)
        self.i_charge = max(100, 500 + 250 * hiver + 200 * soir + 60 * self.alea_charge)
        self.u_htb = U_HTB_NOMINALE - 1.0 * soir + 1.0 * self.alea_htb   # plus basse aux heures de pointe
        self.temperature = 12 - 9 * hiver + 4 * math.cos(2 * math.pi * (self.heure - 15) / 24)
