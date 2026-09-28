"""Table des adresses Modbus : LE SEUL endroit où les adresses sont écrites.

Le serveur Modbus est dans l'automate (automate.py). La simulation et
Node-RED sont des clients qui viennent lire et écrire dans sa mémoire.
Adresses en base 0. Le tableau est dans docs/specification.md.
"""

PORT = 5020                 # port TCP du serveur (502 demande les droits administrateur)
NB_COILS = 16
NB_REGISTRES = 32

# ---------------------------------------------------------------------------
# COILS (bits)
# ---------------------------------------------------------------------------
CO_ORDRE_MONTER = 0         # ordre prise + 1                    automate -> simulation
CO_ORDRE_DESCENDRE = 1      # ordre prise - 1                    automate -> simulation
CO_MANOEUVRE_EN_COURS = 2   # régleur en mouvement               simulation -> automate
CO_MODE_AUTO = 3            # 1 = AUTO, 0 = MANUEL               IHM <-> automate
CO_BP_PLUS = 4              # bouton + (impulsion)               IHM -> automate
CO_BP_MOINS = 5             # bouton - (impulsion)               IHM -> automate
CO_ACQUITTEMENT = 6         # acquittement (impulsion)           IHM -> automate
CO_DEFAUT_REGLEUR = 7       # alarme défaut régleur              automate -> IHM
CO_BLOCAGE = 8              # U_HTA trop basse : régulation bloquée  automate -> IHM
CO_BUTEE = 9                # prise 1 ou 17                      automate -> IHM
CO_VARIATIONS_AUTO = 10     # 1 = vie réelle simulée, 0 = curseurs de l'IHM   IHM -> simulation

# ---------------------------------------------------------------------------
# HOLDING REGISTERS (mots de 16 bits)
# ---------------------------------------------------------------------------
# Mesures                                                         simulation -> automate
HR_U_HTA = 0                # kV x 100 (2050 = 20,50 kV)
HR_PRISE = 1                # 1 à 17
# Environnement : écrits par la simulation en variations auto,
# par les curseurs de l'IHM sinon.
HR_U_HTB = 2                # kV x 10 (630 = 63,0 kV)
HR_I_CHARGE = 3             # A
# Informations de la simulation                                   simulation -> IHM
HR_TEMPERATURE = 4          # °C x 10, entier signé (complément à 2, type INT)
HR_HEURE = 5                # minutes depuis minuit (0 à 1439)
HR_JOUR = 6                 # jour de l'année (1 à 365) ; l'IHM peut l'écrire pour changer de saison
# Réglages                                                        IHM -> automate
HR_CONSIGNE = 10            # kV x 100
HR_BANDE_MORTE = 11         # % x 100 (100 = 1,00 %)
HR_TEMPO_1 = 12             # s : temporisation de la 1re manœuvre
HR_TEMPO_2 = 13             # s : temporisation des manœuvres suivantes
# État de l'automate                                              automate -> IHM
HR_ETAPE = 20               # étape du Grafcet (ETAPE_*)
HR_TEMPS_RESTANT = 21       # s avant l'ordre (étape TEMPORISATION)
HR_NB_MANOEUVRES = 22       # compteur de manœuvres
HR_MOT_DE_VIE = 23          # +1 à chaque cycle

# Codage de HR_ETAPE (étapes du Grafcet du régulateur)
ETAPE_ATTENTE = 0
ETAPE_TEMPORISATION = 1
ETAPE_ORDRE = 2
ETAPE_MANOEUVRE = 3
ETAPE_DEFAUT = 4

# Échelles
ECHELLE_U_HTA = 100         # valeur Modbus = kV x 100
ECHELLE_U_HTB = 10          # valeur Modbus = kV x 10
ECHELLE_BANDE_MORTE = 100   # valeur Modbus = % x 100
ECHELLE_TEMPERATURE = 10    # valeur Modbus = °C x 10
