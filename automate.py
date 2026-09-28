"""Automate de régulation de tension.

Partie 1 (ce qui suit) : le PROGRAMME de l'automate, sans aucun réseau.
    - Tempo       : imite le bloc TON d'un automate ;
    - Regulateur  : le Grafcet du régulateur (voir docs/specification.md).
    On peut le tester seul avec pytest (tests/test_automate.py).

Partie 2 (étape 4) : serveur Modbus TCP + cycle LIRE -> TRAITER -> ÉCRIRE.

Le temps est compté en millisecondes (dt_ms = durée du cycle).
"""

from mapping import (
    ETAPE_ATTENTE,
    ETAPE_DEFAUT,
    ETAPE_MANOEUVRE,
    ETAPE_ORDRE,
    ETAPE_TEMPORISATION,
)

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
