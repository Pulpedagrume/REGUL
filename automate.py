"""Automate de régulation de tension.

Partie 1 (ce qui suit) : le PROGRAMME de l'automate, sans aucun réseau.
    - Tempo       : imite le bloc TON d'un automate ;
    - Regulateur  : le Grafcet du régulateur (voir docs/specification.md).
    On peut le tester seul avec pytest (tests/test_automate.py).

Partie 2 : serveur Modbus TCP + cycle LIRE -> TRAITER -> ÉCRIRE.
    Lancement :  uv run automate.py

    Le serveur Modbus (la « mémoire » de l'automate) tourne dans un thread.
    La simulation et Node-RED viennent y lire et écrire. Le programme de
    l'automate y accède avec un client Modbus, comme les autres : il n'y a
    qu'une seule façon d'échanger dans tout le projet.

Le temps est compté en millisecondes (dt_ms = durée du cycle).
"""

import threading
import time

from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ModbusException
from pymodbus.server import StartTcpServer
from pymodbus.simulator import DataType, SimData, SimDevice

from mapping import (
    CO_ACQUITTEMENT,
    CO_BLOCAGE,
    CO_BP_MOINS,
    CO_BP_PLUS,
    CO_BUTEE,
    CO_DEFAUT_REGLEUR,
    CO_MANOEUVRE_EN_COURS,
    CO_MODE_AUTO,
    CO_ORDRE_DESCENDRE,
    CO_ORDRE_MONTER,
    ECHELLE_BANDE_MORTE,
    ECHELLE_U_HTA,
    ETAPE_ATTENTE,
    ETAPE_DEFAUT,
    ETAPE_MANOEUVRE,
    ETAPE_ORDRE,
    ETAPE_TEMPORISATION,
    HR_BANDE_MORTE,
    HR_CONSIGNE,
    HR_ETAPE,
    HR_MOT_DE_VIE,
    HR_NB_MANOEUVRES,
    HR_PRISE,
    HR_TEMPO_1,
    HR_TEMPO_2,
    HR_TEMPS_RESTANT,
    HR_U_HTA,
    NB_COILS,
    NB_REGISTRES,
    PORT,
)

CYCLE_MS = 100

# Constantes (non réglables depuis l'IHM)
PRISE_MIN = 1
PRISE_MAX = 17
SEUIL_BLOCAGE_KV = 16.0         # 80 % de 20 kV : en dessous, on ne régule plus
DUREE_MAX_MANOEUVRE_MS = 10000  # au-delà : défaut régleur

# Réglages par défaut et plages autorisées (unités physiques)
CONSIGNE_KV = 20.5
BANDE_MORTE_PC = 1.0            # ± 1 %
TEMPO_1_S = 10                  # 1re manœuvre (30 s en réel, raccourci pour la démo)
TEMPO_2_S = 5                   # manœuvres suivantes (10 s en réel)
PLAGE_CONSIGNE_KV = (19.0, 22.0)
PLAGE_BANDE_MORTE_PC = (0.7, 5.0)   # > 0,625 % : sinon le régleur « pompe »
PLAGE_TEMPO_S = (1, 120)


def borner(valeur, plage):
    """Ramène une valeur dans sa plage (mini, maxi)."""
    mini, maxi = plage
    return max(mini, min(valeur, maxi))


class Tempo:
    """Temporisation au travail, comme le bloc TON.

    Tant que l'entrée est vraie, le temps écoulé augmente.
    La sortie q passe à 1 quand le temps écoulé atteint la durée.
    Si l'entrée retombe, tout est remis à zéro.
    """

    def __init__(self, duree_ms):
        self.duree_ms = duree_ms
        self.ecoule_ms = 0
        self.q = False

    def cycle(self, entree, dt_ms):
        if entree:
            self.ecoule_ms = min(self.ecoule_ms + dt_ms, self.duree_ms)
        else:
            self.ecoule_ms = 0
        self.q = entree and self.ecoule_ms >= self.duree_ms
        return self.q


class Regulateur:
    """Régulateur de tension : Grafcet à 5 étapes (ATTENTE ... DÉFAUT)."""

    def __init__(self):
        # Réglages
        self.consigne = CONSIGNE_KV
        self.bande_morte = BANDE_MORTE_PC
        self.tempo_1 = TEMPO_1_S
        self.tempo_2 = TEMPO_2_S
        # Mémoires internes
        self.etape = ETAPE_ATTENTE
        self.sens = 0               # +1 = monter, -1 = descendre
        self.premiere = True        # la prochaine manœuvre est la 1re (tempo 1)
        self.tempo = Tempo(TEMPO_1_S * 1000)
        self.tempo_defaut = Tempo(DUREE_MAX_MANOEUVRE_MS)
        # Sorties
        self.mode_auto = True
        self.ordre_monter = False
        self.ordre_descendre = False
        self.defaut = False
        self.blocage = False
        self.butee = False
        self.nb_manoeuvres = 0

    def regler(self, consigne, bande_morte, tempo_1, tempo_2):
        """Applique les réglages, ramenés dans leur plage."""
        self.consigne = borner(consigne, PLAGE_CONSIGNE_KV)
        self.bande_morte = borner(bande_morte, PLAGE_BANDE_MORTE_PC)
        self.tempo_1 = borner(tempo_1, PLAGE_TEMPO_S)
        self.tempo_2 = borner(tempo_2, PLAGE_TEMPO_S)

    def temps_restant(self):
        """Secondes restantes avant l'ordre (0 hors de l'étape TEMPORISATION)."""
        if self.etape != ETAPE_TEMPORISATION:
            return 0
        restant_ms = self.tempo.duree_ms - self.tempo.ecoule_ms
        return (restant_ms + 999) // 1000      # arrondi à la seconde supérieure

    def cycle(self, u_hta, prise, manoeuvre_en_cours, mode_auto,
              bp_plus, bp_moins, acquittement, dt_ms):
        """Un cycle de l'automate : calculs, évolution du Grafcet, actions."""

        # ---------- 1. Calculs ----------
        haut = self.consigne * (1 + self.bande_morte / 100)
        bas = self.consigne * (1 - self.bande_morte / 100)
        if u_hta < bas:
            besoin = +1             # tension trop basse : il faut monter
        elif u_hta > haut:
            besoin = -1             # tension trop haute : il faut descendre
        else:
            besoin = 0              # dans la bande
            self.premiere = True    # la prochaine manœuvre sera une « 1re »

        self.blocage = u_hta < SEUIL_BLOCAGE_KV
        self.butee = prise <= PRISE_MIN or prise >= PRISE_MAX
        # Le mode AUTO est refusé tant que le défaut n'est pas acquitté
        self.mode_auto = mode_auto and self.etape != ETAPE_DEFAUT

        def possible(sens):
            """Vrai si on peut bouger dans ce sens sans dépasser les butées."""
            return (sens == +1 and prise < PRISE_MAX) or (sens == -1 and prise > PRISE_MIN)

        # ---------- 2. Évolution du Grafcet ----------
        if self.etape == ETAPE_ATTENTE:
            if self.mode_auto:
                if besoin != 0 and not self.blocage and possible(besoin):
                    self.sens = besoin
                    self.etape = ETAPE_TEMPORISATION
            elif bp_plus and possible(+1):
                self.sens = +1
                self.etape = ETAPE_ORDRE
            elif bp_moins and possible(-1):
                self.sens = -1
                self.etape = ETAPE_ORDRE

        elif self.etape == ETAPE_TEMPORISATION:
            if not self.mode_auto or besoin != self.sens or self.blocage:
                self.etape = ETAPE_ATTENTE      # retour dans la bande, changement de sens...
            elif self.tempo.q:
                self.etape = ETAPE_ORDRE

        elif self.etape == ETAPE_ORDRE:
            if manoeuvre_en_cours:              # le régleur a pris l'ordre
                self.etape = ETAPE_MANOEUVRE

        elif self.etape == ETAPE_MANOEUVRE:
            if not manoeuvre_en_cours:          # fin de la manœuvre
                self.etape = ETAPE_ATTENTE
                self.nb_manoeuvres += 1
                self.premiere = False

        elif self.etape == ETAPE_DEFAUT:
            if acquittement and not manoeuvre_en_cours:
                self.etape = ETAPE_ATTENTE

        # Manœuvre trop longue (étapes ORDRE et MANŒUVRE) : défaut régleur
        if self.etape in (ETAPE_ORDRE, ETAPE_MANOEUVRE) and self.tempo_defaut.q:
            self.etape = ETAPE_DEFAUT
            self.mode_auto = False

        # ---------- 3. Actions ----------
        tempo_s = self.tempo_1 if self.premiere else self.tempo_2
        self.tempo.duree_ms = tempo_s * 1000
        self.tempo.cycle(self.etape == ETAPE_TEMPORISATION, dt_ms)
        self.tempo_defaut.cycle(self.etape in (ETAPE_ORDRE, ETAPE_MANOEUVRE), dt_ms)
        self.ordre_monter = self.etape == ETAPE_ORDRE and self.sens == +1
        self.ordre_descendre = self.etape == ETAPE_ORDRE and self.sens == -1
        self.defaut = self.etape == ETAPE_DEFAUT


# ---------------------------------------------------------------------------
# Partie 2 : serveur Modbus et cycle de l'automate
# ---------------------------------------------------------------------------
def demarrer_serveur():
    """Crée la mémoire Modbus (coils + registres) et lance le serveur dans un thread."""
    memoire = SimDevice(
        id=0,  # 0 = répond quel que soit le numéro d'esclave demandé
        simdata=(
            [SimData(0, count=NB_COILS, values=False, datatype=DataType.BITS)],       # coils
            [SimData(0, count=1, values=False, datatype=DataType.BITS)],              # discrete inputs (inutilisés)
            [SimData(0, count=NB_REGISTRES, values=0, datatype=DataType.REGISTERS)],  # holding registers
            [SimData(0, count=1, values=0, datatype=DataType.REGISTERS)],             # input registers (inutilisés)
        ),
    )
    serveur = threading.Thread(
        target=StartTcpServer,
        args=(memoire,),
        kwargs={"address": ("0.0.0.0", PORT)},
        daemon=True,  # le thread s'arrête avec le programme
    )
    serveur.start()


def reglages_en_registres(reg):
    """Les 4 réglages appliqués, dans l'ordre de la mémoire (HR 10 à 13)."""
    return [round(reg.consigne * ECHELLE_U_HTA), round(reg.bande_morte * ECHELLE_BANDE_MORTE),
            reg.tempo_1, reg.tempo_2]


def main():
    demarrer_serveur()
    time.sleep(0.5)  # laisse au serveur le temps de démarrer

    client = ModbusTcpClient("127.0.0.1", port=PORT)
    client.connect()

    regulateur = Regulateur()
    # Valeurs de départ visibles par l'IHM
    client.write_registers(HR_CONSIGNE, reglages_en_registres(regulateur))
    client.write_coil(CO_MODE_AUTO, True)
    print(f"Automate démarré : serveur Modbus TCP sur le port {PORT}, cycle {CYCLE_MS} ms")

    mot_de_vie = 0
    precedent = time.monotonic()

    while True:
        debut = time.monotonic()
        dt_ms = round((debut - precedent) * 1000)  # durée réelle depuis le cycle précédent
        precedent = debut
        try:
            # ---------- 1. LECTURE DES ENTRÉES ----------
            co = client.read_coils(0, count=NB_COILS).bits
            hr = client.read_holding_registers(0, count=NB_REGISTRES).registers

            # ---------- 2. TRAITEMENT ----------
            lus = hr[HR_CONSIGNE:HR_CONSIGNE + 4]
            regulateur.regler(lus[0] / ECHELLE_U_HTA, lus[1] / ECHELLE_BANDE_MORTE, lus[2], lus[3])
            regulateur.cycle(u_hta=hr[HR_U_HTA] / ECHELLE_U_HTA,
                             prise=hr[HR_PRISE],
                             manoeuvre_en_cours=co[CO_MANOEUVRE_EN_COURS],
                             mode_auto=co[CO_MODE_AUTO],
                             bp_plus=co[CO_BP_PLUS],
                             bp_moins=co[CO_BP_MOINS],
                             acquittement=co[CO_ACQUITTEMENT],
                             dt_ms=dt_ms)

            # ---------- 3. ÉCRITURE DES SORTIES ----------
            client.write_coils(CO_ORDRE_MONTER, [regulateur.ordre_monter, regulateur.ordre_descendre])
            client.write_coils(CO_DEFAUT_REGLEUR, [regulateur.defaut, regulateur.blocage, regulateur.butee])
            mot_de_vie = (mot_de_vie + 1) % 65536
            client.write_registers(HR_ETAPE, [regulateur.etape, regulateur.temps_restant(),
                                              regulateur.nb_manoeuvres, mot_de_vie])

            # Sur défaut, l'automate passe lui-même en MANUEL
            if regulateur.mode_auto != co[CO_MODE_AUTO]:
                client.write_coil(CO_MODE_AUTO, regulateur.mode_auto)

            # Réglages hors plage : on réécrit la valeur réellement appliquée
            if lus != reglages_en_registres(regulateur):
                client.write_registers(HR_CONSIGNE, reglages_en_registres(regulateur))

            # Les boutons et l'acquittement sont des impulsions : on les remet à 0
            for adresse in (CO_BP_PLUS, CO_BP_MOINS, CO_ACQUITTEMENT):
                if co[adresse]:
                    client.write_coil(adresse, False)

        except ModbusException as erreur:
            print(f"Erreur Modbus : {erreur}")
            client.connect()

        # Attendre la fin du cycle
        duree = time.monotonic() - debut
        time.sleep(max(0.0, CYCLE_MS / 1000 - duree))


if __name__ == "__main__":
    main()
