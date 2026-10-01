"""Simulation du poste : réseau HTB, charge, transformateur et régleur en charge.

Partie 1 (ce qui suit) : le MODÈLE, sans aucun réseau. On peut le tester seul
    avec pytest (tests/test_simulation.py).
    - tension_hta : la formule du transformateur ;
    - Regleur     : le régleur en charge (une manœuvre dure 5 s) ;
    - VieReelle   : U_HTB, charge et température qui varient comme dans la vraie vie.

Partie 2 : client Modbus qui fait tourner le modèle.
    Lancement :  uv run simulation.py   (après avoir lancé l'automate)

Le temps est compté en secondes (dt_s = durée du cycle).
"""

import math
import random
import time

from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ModbusException

from mapping import (
    CO_MANOEUVRE_EN_COURS,
    CO_ORDRE_DESCENDRE,
    CO_ORDRE_MONTER,
    CO_VARIATIONS_AUTO,
    ECHELLE_TEMPERATURE,
    ECHELLE_U_HTA,
    ECHELLE_U_HTB,
    HR_I_CHARGE,
    HR_JOUR,
    HR_PRISE_VISEE,
    HR_TEMPERATURE,
    HR_U_HTA,
    HR_U_HTB,
    NB_COILS,
    NB_REGISTRES,
    PORT,
)

PAS_S = 0.1                 # cycle de la simulation

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
CHARGE_MAX = 950            # A : un soir de janvier à 19 h

# Courbe de charge type d'une journée, heure par heure (de 0 h à 23 h), en part de la pointe :
# creux de nuit vers 4 h, pointe du matin (lever, chauffe-eau), plateau de la journée,
# pointe du soir à 19 h (cuisine, éclairage, chauffage)
PROFIL_HORAIRE = [0.55, 0.50, 0.47, 0.45, 0.45, 0.48, 0.58, 0.75, 0.85, 0.85, 0.82, 0.82,
                  0.85, 0.82, 0.78, 0.76, 0.78, 0.88, 0.98, 1.00, 0.95, 0.85, 0.72, 0.62]


def tension_hta(u_htb, prise, i_charge):
    """Tension du jeu de barres HTA (kV) : rapport de transformation moins chute en charge."""
    rapport = U_HTA_NOMINALE * (u_htb / U_HTB_NOMINALE)
    return max(0.0, rapport * (1 + PAS_PRISE * (prise - PRISE_NEUTRE)) - K_CHUTE * i_charge)  # jamais négative


class Regleur:
    """Régleur en charge : reçoit un ordre, met 5 s à changer de prise."""

    def __init__(self):
        self.prise = PRISE_NEUTRE
        self.en_manoeuvre = False
        self.sens = 0               # +1 = monter, -1 = descendre
        self.chrono = 0.0           # temps écoulé depuis le début de la manœuvre

    def prise_visee(self):
        """Prise vers laquelle le régleur se déplace pendant une manœuvre, 0 au repos (pour l'IHM)."""
        return self.prise + self.sens if self.en_manoeuvre else 0

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


def profil_journalier(heure):
    """Part de la pointe consommée à cette heure (0 à 1), entre deux valeurs du tableau."""
    h = int(heure) % 24
    fraction = heure - int(heure)
    return PROFIL_HORAIRE[h] + (PROFIL_HORAIRE[(h + 1) % 24] - PROFIL_HORAIRE[h]) * fraction


class VieReelle:
    """Fait varier U_HTB, la charge et la température comme dans la vraie vie.

    Deux cycles se superposent : le jour (courbe de charge type, avec ses
    pointes du matin et du soir) et l'année (charge forte en hiver à cause du
    chauffage électrique, faible en été). On y ajoute un petit aléa lent
    (marche aléatoire) pour que deux journées ne soient pas identiques.
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
        # Jour : courbe de charge type. Année : +1 à la mi-janvier, -1 à la mi-juillet
        profil = profil_journalier(self.heure)
        hiver = math.cos(2 * math.pi * (self.jour - 15) / 365)
        saison = 0.6 + 0.2 * (1 + hiver)                    # 1 en hiver, 0,6 en été
        self.i_charge = max(100, CHARGE_MAX * profil * saison + 50 * self.alea_charge)
        self.u_htb = U_HTB_NOMINALE + 1.0 - 2.0 * profil + 1.0 * self.alea_htb   # plus basse aux heures de pointe
        self.temperature = 12 - 9 * hiver + 4 * math.cos(2 * math.pi * (self.heure - 15) / 24)


# ---------------------------------------------------------------------------
# Partie 2 : client Modbus
# ---------------------------------------------------------------------------
def main():
    client = ModbusTcpClient("127.0.0.1", port=PORT)
    client.connect()

    vie = VieReelle()
    regleur = Regleur()
    # Valeurs de départ : vie réelle en marche, calendrier au jour de départ
    client.write_coil(CO_VARIATIONS_AUTO, True)
    client.write_register(HR_JOUR, vie.jour)
    dernier_jour = vie.jour
    print(f"Simulation démarrée : connectée à l'automate sur le port {PORT}")

    precedent = time.monotonic()
    dernier_affichage = 0.0

    while True:
        debut = time.monotonic()
        dt_s = debut - precedent        # durée réelle depuis le cycle précédent
        precedent = debut
        try:
            # ---------- 1. LECTURE : ordres de l'automate, curseurs et jour de l'IHM ----------
            co = client.read_coils(0, count=NB_COILS).bits
            hr = client.read_holding_registers(0, count=NB_REGISTRES).registers
            vie_auto = co[CO_VARIATIONS_AUTO]
            jour_lu = hr[HR_JOUR]
            if jour_lu != dernier_jour:         # l'IHM a changé le jour : on saute de saison
                vie.jour = max(1, min(365, jour_lu))

            # ---------- 2. ÉVOLUTION DU POSTE ----------
            if vie_auto:
                vie.cycle(dt_s)                 # sinon les curseurs de l'IHM fixent U_HTB et la charge
                u_htb, i_charge = vie.u_htb, vie.i_charge
            else:
                u_htb, i_charge = hr[HR_U_HTB] / ECHELLE_U_HTB, hr[HR_I_CHARGE]
            regleur.cycle(co[CO_ORDRE_MONTER], co[CO_ORDRE_DESCENDRE], dt_s)
            u_hta = tension_hta(u_htb, regleur.prise, i_charge)

            # ---------- 3. ÉCRITURE : mesures vers l'automate et l'IHM ----------
            client.write_coil(CO_MANOEUVRE_EN_COURS, regleur.en_manoeuvre)
            client.write_registers(HR_U_HTA, [round(u_hta * ECHELLE_U_HTA), regleur.prise])
            client.write_register(HR_PRISE_VISEE, regleur.prise_visee())
            if vie_auto:                        # en mode curseurs, ce sont les curseurs qui écrivent
                client.write_registers(HR_U_HTB, [round(u_htb * ECHELLE_U_HTB), round(i_charge)])
                client.write_registers(HR_TEMPERATURE, [
                    round(vie.temperature * ECHELLE_TEMPERATURE) % 65536,   # entier signé : complément à 2
                    round(vie.heure * 60),
                ])
            if vie.jour != jour_lu:             # on n'écrit le jour que s'il a changé
                client.write_register(HR_JOUR, vie.jour)
            dernier_jour = vie.jour

            if debut - dernier_affichage > 5:
                dernier_affichage = debut
                print(f"{int(vie.heure):02d}h jour {vie.jour:3d} | HTB {u_htb:5.2f} kV | "
                      f"charge {i_charge:4.0f} A | prise {regleur.prise:2d} | U HTA {u_hta:5.2f} kV")

        except ModbusException as erreur:
            print(f"Erreur Modbus : {erreur}")
            client.connect()

        # Attendre la fin du cycle
        duree = time.monotonic() - debut
        time.sleep(max(0.0, PAS_S - duree))


if __name__ == "__main__":
    main()
