# Spécification — Régulation de tension d'un transformateur HTB/HTA

> Version 0 (étape 1) : **à valider**. Les valeurs marquées ❓ sont des
> propositions qui dépendent des réponses aux questions.

## 1. Système étudié

Un transformateur de poste source **63 kV / 20 kV** équipé d'un **régleur en
charge** (17 prises). Un automate agit sur la prise pour maintenir la tension
du jeu de barres HTA autour d'une consigne.

```
   Réseau HTB 63 kV (varie de ± 5 %)
          │
       ┌──┴──┐
       │ TR  │◄──── régleur en charge : prise 1 … 17 (9 = neutre)
       └──┬──┘         ▲ ordres monter / descendre
          │            │ position + « manœuvre en cours »
   ═══════╪══════ Jeu de barres HTA ───► mesure U_HTA ───► AUTOMATE
          │
       Charge du poste (courant I_charge variable)
```

Trois programmes :

| Programme | Rôle |
|---|---|
| `automate` (Python) | Serveur Modbus TCP (port 5020) + cycle LIRE → TRAITER → ÉCRIRE toutes les 100 ms ❓ |
| `simulation` (Python) | Client Modbus : fait évoluer U_HTB, I_charge et la prise ; calcule U_HTA |
| IHM Node-RED | Client Modbus : affichage, réglages, boutons, alarmes, courbes |

## 2. Modèle de la simulation

```
U_HTA = 20 kV × (U_HTB / 63) × (1 + 0,0125 × (prise − 9)) − k × I_charge
```

| Grandeur | Valeur proposée |
|---|---|
| Pas d'une prise | 1,25 % (plage totale ± 10 %) |
| k (chute de tension) | 0,001 kV/A (soit 1 V par ampère : 1 kV à 1000 A) ❓ |
| U_HTB | 63 kV × (1 + 0,05 × sin(2π t / 240 s)) ❓ |
| I_charge | 500 A + 300 A × sin(2π t / 180 s) (de 200 à 800 A) ❓ |
| Durée d'une manœuvre | 5 s |
| Prise au démarrage | 9 (neutre) |

Au démarrage (U_HTB = 63 kV, I = 500 A, prise 9) : U_HTA = 19,5 kV, donc
1 kV sous la consigne de 20,5 kV → l'automate doit monter d'environ 4 prises.

Convention : **monter** = prise + 1 = la tension HTA **augmente**.

Régleur simulé : sur un ordre (monter ou descendre), s'il n'est pas en butée
et pas déjà en manœuvre, il met « manœuvre en cours » à 1 pendant 5 s, puis
change la prise et remet « manœuvre en cours » à 0.

Pour les tests de l'IHM (❓ voir questions) :
- **Régleur bloqué** : la manœuvre ne se termine jamais → défaut régleur.
- **Perte de la tension HTB** : U_HTB = 0 → blocage de la régulation.

## 3. Entrées / sorties de l'automate

| Entrées | Origine | Sorties | Destination |
|---|---|---|---|
| Tension U_HTA | simulation | Ordre monter | simulation (régleur) |
| Position de la prise (1 à 17) | simulation | Ordre descendre | simulation (régleur) |
| Manœuvre en cours | simulation | Étape du Grafcet, temps restant | IHM |
| Mode AUTO / MANUEL | IHM | Alarme défaut régleur | IHM |
| Boutons + / − (impulsions) | IHM | Voyant blocage sous-tension | IHM |
| Acquittement (impulsion) | IHM | Voyant butée (prise 1 ou 17) | IHM |
| Réglages (consigne, bande morte, tempos) | IHM | Compteur de manœuvres, mot de vie | IHM |

## 4. Réglages (modifiables depuis l'IHM)

| Réglage | Défaut | Plage | Unité Modbus |
|---|---|---|---|
| Consigne | 20,50 kV | 19,00 – 22,00 kV | kV × 100 (2050) |
| Bande morte | ± 1,00 % | ± 0,70 – 5,00 % | % × 100 (100) |
| Tempo 1re manœuvre | 10 s (réel : 30 s) ❓ | 1 – 120 s | s |
| Tempo manœuvres suivantes | 5 s (réel : 10 s) ❓ | 1 – 120 s | s |

Constantes (dans le code, non réglables) :
- **Durée maxi d'une manœuvre** : 10 s → au-delà, défaut régleur.
- **Seuil de blocage** : U_HTA < 80 % de 20 kV = **16 kV**.

Une valeur hors plage est ramenée dans la plage par l'automate, qui réécrit
la valeur réellement appliquée (même principe que le projet demi-rame).

**Pourquoi une bande morte d'au moins ± 0,7 % ?** Une prise fait varier la
tension de 1,25 %. Si la bande (largeur totale 2 × bande morte) est plus
étroite qu'une prise, une manœuvre fait sauter la tension d'un côté à l'autre
de la bande : le régleur « pompe » sans fin (monte, descend, monte…).
Il faut donc 2 × bande morte > 1,25 %, soit bande morte > 0,625 %.

## 5. Logique du régulateur

Bande : `consigne × (1 − bm)` ≤ U_HTA ≤ `consigne × (1 + bm)`.

- U_HTA **sous** la bande → il faut **monter** ; **au-dessus** → **descendre**.
- Tempo = T1 pour la 1re manœuvre, T2 pour les suivantes. On revient à T1
  dès que la tension rentre dans la bande.
- Jamais d'ordre « monter » en prise 17, ni « descendre » en prise 1.
- Un seul ordre à la fois : on attend la fin de la manœuvre.
- Blocage (mode AUTO seulement) si U_HTA < 16 kV : pas de nouvel ordre.
- Défaut régleur : ordre donné depuis plus de 10 s sans fin de manœuvre →
  alarme, passage en MANUEL, ordres interdits jusqu'à l'acquittement
  (accepté seulement si la manœuvre n'est plus en cours).
- En MANUEL : un appui sur + ou − donne un ordre (mêmes conditions de butée,
  de manœuvre en cours et de défaut).
- L'ordre est **maintenu jusqu'à ce que le régleur réponde** « manœuvre en
  cours » (échange « ordre / accusé »), puis retombe.

### Grafcet du régulateur

Un seul Grafcet pour les deux modes : seule l'entrée dans la manœuvre change.

```
            ┌────────────────────────────────┐
            ▼                                │
     ┌─────────────┐                         │
     ║ 0  ATTENTE  ║                         │
     └──────┬──────┘                         │
   ┌────────┴──────────────────┐             │
   │ AUTO · hors bande          │ MANUEL · (BP+ · prise<17
   │ · non bloqué · pas butée   │    + BP− · prise>1)
   ▼                            │             │
┌──────────────────┐            │             │
│ 1  TEMPORISATION │ Tempo T1/T2│             │
└────────┬─────────┘            │             │
   ┌─────┴───────────┐          │             │
   │ tempo écoulée   │ dans la bande + MANUEL │
   │                 │ + bloqué + butée ──────┤ (retour à 0)
   ▼                 ▼                        │
┌──────────────────────────────┐              │
│ 2  ORDRE   ordre monter ou   │ ◄────────────┘ (depuis 0, en MANUEL)
│            descendre = 1     │  Tempo défaut 10 s lancée
└──────────────┬───────────────┘
               │ manœuvre en cours = 1
               ▼
┌──────────────────────────────┐
│ 3  MANŒUVRE (ordre retombé)  │
└──────────────┬───────────────┘
               │ manœuvre en cours = 0  → retour à 0 (la suivante utilisera T2)
               └──────────────────────────────────────► 0

  Depuis 2 ou 3 : tempo défaut écoulée (10 s) ──► 4  DÉFAUT RÉGLEUR
                                                   (alarme, mode = MANUEL)
  Depuis 4 : acquittement · manœuvre en cours = 0 ──► 0
```

| Étape | Code | Actions |
|---|---|---|
| 0 ATTENTE | 0 | aucune |
| 1 TEMPORISATION | 1 | tempo T1 ou T2 ; temps restant affiché |
| 2 ORDRE | 2 | ordre monter ou descendre ; tempo défaut |
| 3 MANŒUVRE | 3 | tempo défaut (continue) |
| 4 DÉFAUT RÉGLEUR | 4 | alarme ; mode forcé en MANUEL |

## 6. Mapping Modbus (adresses en base 0)

Un seul fichier `mapping.py` contiendra toutes les adresses. Seuls les coils
et les holding registers sont utilisés.

### Coils (bits)

| Adr. | Nom | Sens | Rôle |
|---|---|---|---|
| 0 | `CO_ORDRE_MONTER` | automate → simulation | ordre prise + 1 |
| 1 | `CO_ORDRE_DESCENDRE` | automate → simulation | ordre prise − 1 |
| 2 | `CO_MANOEUVRE_EN_COURS` | simulation → automate | régleur en mouvement |
| 3 | `CO_MODE_AUTO` | IHM ↔ automate | 1 = AUTO, 0 = MANUEL (l'automate le met à 0 sur défaut) |
| 4 | `CO_BP_PLUS` | IHM → automate | impulsion, remise à 0 par l'automate |
| 5 | `CO_BP_MOINS` | IHM → automate | impulsion, remise à 0 par l'automate |
| 6 | `CO_ACQUITTEMENT` | IHM → automate | impulsion, remise à 0 par l'automate |
| 7 | `CO_DEFAUT_REGLEUR` | automate → IHM | alarme |
| 8 | `CO_BLOCAGE_SOUS_TENSION` | automate → IHM | U_HTA < 16 kV |
| 9 | `CO_BUTEE` | automate → IHM | prise = 1 ou 17 |
| 10 | `CO_TEST_REGLEUR_BLOQUE` | IHM → simulation | test : le régleur ne finit plus sa manœuvre ❓ |
| 11 | `CO_TEST_PERTE_HTB` | IHM → simulation | test : U_HTB = 0 ❓ |

### Holding registers (mots de 16 bits, non signés)

| Adr. | Nom | Sens | Unité |
|---|---|---|---|
| 0 | `HR_U_HTA` | simulation → automate | kV × 100 (2050 = 20,50 kV) |
| 1 | `HR_PRISE` | simulation → automate | 1 à 17 |
| 2 | `HR_U_HTB` | simulation → IHM | kV × 10 (630 = 63,0 kV) |
| 3 | `HR_I_CHARGE` | simulation → IHM | A |
| 10 | `HR_CONSIGNE` | IHM → automate | kV × 100 |
| 11 | `HR_BANDE_MORTE` | IHM → automate | % × 100 |
| 12 | `HR_TEMPO_1` | IHM → automate | s |
| 13 | `HR_TEMPO_2` | IHM → automate | s |
| 20 | `HR_ETAPE` | automate → IHM | 0 à 4 (Grafcet) |
| 21 | `HR_TEMPS_RESTANT` | automate → IHM | s (tempo en cours) |
| 22 | `HR_NB_MANOEUVRES` | automate → IHM | compteur |
| 23 | `HR_MOT_DE_VIE` | automate → IHM | +1 à chaque cycle |

## 7. Hors périmètre (simplifications)

- Compensation de la chute en ligne (LDC), transformateurs en parallèle.
- Blocage sur surtension, sur surintensité.
- Usure / comptage d'entretien du régleur (seul un compteur est affiché).
- La tension HTB et la charge ne passent pas par l'automate (il ne mesure que U_HTA et la prise).

## 8. Organisation des fichiers (proposée)

```
REGUL/
├── pyproject.toml            dépendance : pymodbus (pytest en dev)
├── README.md                 installation, lancement (3 terminaux), démo
├── docs/specification.md     ce document (mapping inclus)
├── src/regul/
│   ├── mapping.py            TOUTES les adresses Modbus
│   ├── regulateur.py         Tempo + Regulateur (PUR, sans réseau)
│   ├── modele.py             modèle transfo + régleur (PUR, sans réseau)
│   ├── automate.py           serveur Modbus + cycle LIRE → TRAITER → ÉCRIRE
│   └── simulation.py         client Modbus qui fait tourner le modèle
├── tests/
│   ├── test_regulateur.py
│   └── test_modele.py
└── node-red/
    ├── package.json          dashboard 2.0 + contrib-modbus + override
    └── flows.json
```
