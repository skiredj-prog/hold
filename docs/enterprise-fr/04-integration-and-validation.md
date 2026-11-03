# HOLD — intégration et validation

> **Portée de ce guide.** Il décrit le code du dépôt HOLD examiné sur `main`, commit `d4ab8acf11f5cea409104753d195370a0769c156`. HOLD est ici un prototype de recherche, pas un produit de production. L’adaptateur hôtelier fourni est synthétique : aucune réservation réelle n’est effectuée dans ce dépôt.

## 1. Installation et premiers contrôles

HOLD requiert Python ≥ 3.10. Les dépendances principales déclarées dans `pyproject.toml` sont Pydantic ≥ 2.7, PyYAML ≥ 6 et HTTPX ≥ 0.27. L’extra `server` ajoute FastAPI et Uvicorn ; l’extra `dev` ajoute pytest. Depuis la racine du dépôt :

```bash
python -m pip install -e ".[server,dev]"
pytest
```

L’installation éditable expose la commande `hold` via `hold.cli:entry`. Pour inspecter ou valider le profil d’exemple :

```bash
hold profile validate hold/profiles/reservation.hold.yaml
hold profile schema
```

`hold profile validate` valide le schéma et affiche empreinte, révision, autorité, seuil et pouvoirs ; ce contrôle ne prouve ni la validité opérationnelle d’un fournisseur, ni l’identité ou la conformité de l’autorité externe. Le profil peut aussi être fingerprinté ou signé avec `hold profile fingerprint …` ou `hold profile sign … --secret … --by … [--out …]`. Le chargement avec vérification de signature est une option explicite de `Habilitation` (`require_signature=True`, `secret=…`) ; il n’est pas activé par défaut. Les modèles de profil rejettent les champs inconnus (`extra="forbid"`). Voir `hold/profile.py` et `hold/cli.py`.

Le profil `hold/profiles/reservation.hold.yaml` est un exemple de configuration, pas une configuration universelle : son autorité WhatsApp, ses fenêtres de dates, son plafond de 300 EUR, ses actions et ses règles de provenance sont propres à cette démonstration. Les invariants du plancher ne sont pas configurables dans le profil (`hold/profile.py`, `hold/floor.py`).

## 2. Intégrer un agent et ses outils

### Cycle d’intégration

1. Définir un profil `habilitation/v1` adapté au domaine : autorité responsable et canal, pouvoirs explicitement nommés et typés (`read`, `effect`, `status`, `contact`), enveloppes, budgets, identité des effets, dépendances, sources de provenance et règles de réconciliation. Pour chaque `effect`, `identity` est obligatoire. Chaque `reconciliation.status_action` doit désigner un pouvoir `status` déclaré ; chaque dépendance doit désigner un `effect` déclaré (`hold/profile.py`).
2. Instancier `Habilitation(profile, store=…, discerner=…, relay=…)`, puis enregistrer une fonction Python pour chaque outil autorisé, au moyen de `@hab.read`, `@hab.effect`, `@hab.status` ou `@hab.contact`. L’enregistrement vérifie que le pouvoir existe et que son type concorde. La liste manquante est consultable via `hab.missing_tools()` ; ouvrir une mission journalise le profil et le binding d’exécution (agent, discerneur, relais et outils enregistrés).
3. Ouvrir une mission (`hab.open_mission(title)`). Donner à l’agent les outils du runtime plutôt que des fonctions d’effet directes : `mission.tools()` expose les actions `read`/`effect` au format de tool calling fonctionnel de type OpenAI ; les actions `status` et `contact` servent au processus de réconciliation HOLD et ne figurent pas dans cette liste. Acheminer chaque appel du modèle par `mission.dispatch(name, arguments)` (ou `mission.call(name, **attrs)`). Ne pas remettre à l’agent un chemin parallèle vers les identifiants ou credentials du fournisseur.
4. Faire remonter au demandeur de l’outil le `ToolResult` effectivement renvoyé : notamment `REFUSED`, `HELD`, `FAILED`, `OK`, `ALREADY_CONFIRMED`, ou autre état explicite. `UNKNOWN` n’est pas un échec prouvé et ne doit pas être transformé par l’intégrateur en succès, ni déclencher un nouvel effet équivalent. Les effets ne sont confirmés qu’avec une preuve corrélée à l’opération ; la réconciliation utilise le status/support configuré et peut aboutir à une `Decision Request` ou préserver un état non confirmé (`hold/runtime.py`, `hold/outcome.py`). Le contrat de sortie de votre agent doit distinguer ce qui est confirmé de ce qui demeure incertain.
5. Fermer et conserver le journal de mission selon vos besoins. `mission.verify()` ne vérifie que la chaîne. **Dans la révision documentée, le CLI `hold journal verify` termine par une erreur `NameError`;** le rejeu état/décisions est accessible par les fonctions `state_replay` et `verify_decisions` de `hold/replay.py`, comme montré plus bas, mais il s’agit d’API internes provisoires.

Le paramètre de stockage accepte un objet `Store` ou un chemin (`hold/runtime.py`). Un même stockage HOLD est la frontière de coordination testée pour les threads et processus sur un hôte ; des stockages distincts ou des hôtes distincts ne se coordonnent pas. Toute autre voie qui peut exécuter les mêmes credentials fournisseur est hors de cette frontière.

### Adaptateur d’outil : contrats à respecter

- Les lectures renvoient des valeurs observables : les résultats d’outils précédents sont enregistrés afin que le contrôle de provenance puisse vérifier que les attributs d’effet proviennent d’un résultat autorisé. Pour une réservation, le profil d’exemple exige notamment que `provider`, `hotel_id`, `price` et `currency` soient issus d’un même enregistrement renvoyé par `search_hotels`. Ne reconstruisez pas ou ne substituez pas silencieusement ces valeurs dans l’adaptateur.
- Un adaptateur `effect` reçoit `operation_id` injecté par HOLD. Son interface externe devrait l’utiliser comme clé d’idempotence uniquement si le fournisseur garantit réellement ce comportement. Dans le décorateur, `idempotency="operation_id"` est une **déclaration** sur le binding ; `verified_by` est une citation de vérification, pas une validation automatique par HOLD. Le runtime distingue `NOT_DECLARED`, `DECLARED` et `VERIFIED`. N’affirmez jamais « exactly once » : l’at-most-once du runtime vaut à l’intérieur de la frontière et la déduplication côté fournisseur dépend du fournisseur (`hold/runtime.py`, `tests/test_guarantees.py`).
- Un effet doit retourner un `Outcome` typé. Utilisez `Outcome.confirmed(receipt, operation_id=operation_id)` seulement si vous avez une preuve fiable et liée à cette opération ; `Outcome.rejected(reason)` seulement pour un refus explicite avant création ; une issue indéterminée doit rester `UNKNOWN` (par exemple laisser remonter une exception de timeout/connexion, traitée par HOLD comme inconnue). Toute réponse non typée d’un adaptateur d’effet est convertie en inconnue (`hold/runtime.py`, `hold/outcome.py`). Un message d’assistance non corrélé n’est pas une preuve.
- Un outil `status` doit rechercher le résultat avec le `operation_id` et retourner `Outcome.confirmed(receipt, operation_id=…)` avec preuve corrélée ou `Outcome.not_found(…)` si le statut ne permet pas de confirmer. `contact` doit également restituer une réponse reliée à l’opération pour qu’elle puisse constituer une preuve ; une assertion libre de « succès » ou d’« échec » n’est pas suffisante.
- Les annotations de fonctions alimentent le schéma des arguments ; les types pris en charge sont inférés simplement (`int`, `float`, `bool`, sinon chaîne). `operation_id` est retiré du schéma présenté à l’agent, mais injecté par HOLD lors de l’appel d’effet. Si la signature d’un outil ou le contrat du fournisseur est plus complexe, déclarez explicitement `parameters=` et validez les données dans l’adaptateur. HOLD contrôle les propriétés de schéma élémentaires, pas toutes les règles métier ou tous les schémas du fournisseur (`hold/runtime.py`).

### Exemple minimal conforme au dépôt — uniquement pour le fournisseur synthétique

L’exemple suivant reprend le profil et l’adaptateur synthétique déjà employés par les tests ; les opérations n’ont aucun effet réel. Il montre le patron d’intégration, pas une recette permettant de connecter un vrai fournisseur :

```python
from hold.profile import load_profile
from hold.runtime import Habilitation
from hold.relay.base import SimulatedRelay
from hold.testing import SyntheticHotel, wire

profile = load_profile("hold/profiles/reservation.hold.yaml")
hotel = SyntheticHotel(create_mode="respond")  # fournisseur de test synthétique
hab = Habilitation(profile, store=":memory:", relay=SimulatedRelay(), background=False)
wire(hab, hotel)  # adaptateur fourni ; déclare toutes les actions du profil
mission = hab.open_mission("Déplacement de démonstration")

# Il faut observer l’offre autorisée pendant cette mission avant de réserver (provenance I6).
offer = mission.call("search_hotels", city="Lyon").data[0]
args = {k: offer[k] for k in (
    "provider", "hotel_id", "traveler", "check_in", "check_out",
    "price", "currency", "city",
)}
result = mission.dispatch("create_booking", args)
if result.status == "OK":
    itinerary = mission.call(
        "send_itinerary",
        traveler=offer["traveler"],
        booking_reference=result.data["receipt"],
    )
```

Le résultat d’itinéraire dépend d’une réservation confirmée et d’une référence provenant de la preuve ; la démonstration synthétique le respecte. Le code ne traite pas ici tous les cas d’attente, de réconciliation, de clôture ni les erreurs d’intégration : en particulier, `result.status != "OK"` ne peut pas être interprété comme une permission de réessayer. Pour l’adaptateur de test lui-même, voir `hold/testing.py::wire` ; pour les comportements d’agent, voir `hold/demo.py` et `tests/test_agent.py`.

## 3. Règles de conception d’un profil

Un profil délimite une délégation : ce que l’agent peut faire seul, sur quelles valeurs et sous quelle responsabilité. Il ne remplace pas l’implémentation des outils ni les contrôles métier du fournisseur.

- **Pouvoirs et budgets :** déclarer chaque nom d’action, son type et ses limites. Pour un effet, définir `identity` à partir des attributs réellement identifiants d’un effet équivalent ; HOLD normalise casse et espaces pour calculer la clé, et l’identité est délimitée à la mission. Un budget borne les exécutions admises selon les règles du runtime ; `max_corrections` limite les corrections après refus sans effet.
- **Enveloppe et données structurelles :** configurer plafonds monétaires, devise, valeurs permises, fenêtres et champs requis. L’approbation de l’autorité (`APPROVE_ONCE`) ne lève que la règle d’enveloppe, pour les attributs exacts, une seule fois, dans la même révision et avant expiration ; elle ne permet pas de passer outre révocation, expiration, budget, dépendances ou autres invariants. Les champs requis absents, montants illisibles/négatifs ou dates mal formées sont des contrôles structurels non autorisables (`hold/floor.py::structural_effect_violations`, `grant_problem`).
- **Provenance :** pour les effets engageant des valeurs observées, renseigner `provenance` (`attribut` ou `attribut=champ`) et, de préférence, `provenance_source`. HOLD exige que les attributs se trouvent ensemble dans un seul enregistrement retourné antérieurement par une source autorisée ; des valeurs tirées de résultats distincts ne se combinent pas (`hold/floor.py::provenance_match`). Sans source déclarée, la règle ne prouve que la cohérence avec des résultats antérieurs, pas l’autorité de leur origine.
- **Dépendances et réconciliation :** une action dépendante est suspendue tant que l’effet antérieur reste inconnu et n’est permise qu’après confirmation par preuve. Tout `status_action` déclaré pour réconcilier un effet doit correspondre à un pouvoir `status`. Les réglages de `hesitation` définissent l’échelle de discernement, seuil et limites de tours/attente, mais ne rendent pas les preuves facultatives.
- **Autorité, révisions et signatures :** choisir un canal disponible et une autorité réellement responsable. Le profil d’exemple requiert un numéro si le canal est WhatsApp. Les révisions changent l’empreinte et invalident les questions ou approbations liées à l’ancienne version. Les signatures disponibles sont HMAC-SHA256 ; la gestion sûre du secret, des clés, de l’identité et du cycle de vie relève de l’intégrateur et n’est pas établie par le prototype.

## 4. Plan de tests et validation

### Tests unitaires

Exécuter l’ensemble des tests, ou isoler les suites ciblées :

```bash
pytest
pytest tests/test_profile_floor.py tests/test_discernment.py
```

`tests/test_profile_floor.py` couvre validation de profils, empreinte/révision/signature, enveloppes, admissibilité, dépendances et identité d’effet ; `tests/test_discernment.py` teste le discernement. Le test unitaire ne démontre pas la conformité d’un outil ou d’un fournisseur réel.

### Tests d’intégration

```bash
pytest tests/test_runtime.py tests/test_agent.py tests/test_relay_server.py
```

`tests/test_runtime.py` exerce refus, appels dépendants, réponses d’autorité, réconciliation d’une réponse perdue, reprise, concurrence et replay avec le fournisseur synthétique. `tests/test_agent.py` raccorde un agent Llama simulé par faux serveur Ollama ; ce test n’appelle pas un vrai modèle. `tests/test_relay_server.py` teste le webhook, la signature, l’identité de l’expéditeur, l’idempotence des taps et des chemins Cloud via HTTP mocké ; cela ne constitue pas une validation de bout en bout du compte WhatsApp de l’utilisateur.

### Tests e2e et garanties dans la frontière

Le chemin de démonstration avec fournisseur simulé peut être exercé ainsi :

```bash
hold demo --scenario lost-response
hold demo --scenario support
hold demo --scenario never
hold demo --scenario envelope
```

Les scénarios sont synthétiques. Pour les propriétés de concurrence et de portée du stockage :

```bash
pytest tests/test_guarantees.py
```

Les tests couvrent notamment quatre processus partageant une base et constatent un seul dispatch, ainsi que deux bases distinctes qui ne se coordonnent pas (`tests/test_guarantees.py`). Ils ne démontrent pas de déduplication sur un vrai fournisseur ; cette propriété n’existe que si le fournisseur honore l’identifiant d’opération. `tests/test_showcase.py` et `tests/test_studio_parity.py` couvrent aussi l’affichage et les relectures/parités prévues par le dépôt.

### Vérification des journaux

L’identifiant de mission est listé par `hold missions hold.db`. **Dans le commit documenté, ne pas utiliser `hold journal verify hold.db <mission>` comme contrôle réussi :** la commande affiche chaîne, rejeu d’état et décisions puis termine avec `NameError: dr is not defined` dans sa condition de retour (`hold/cli.py::_journal_cmd`, dernière ligne de la branche verify). Ce défaut a été reproduit sur une mission synthétique présente : les contrôles affichés concordaient, mais la commande a échoué. Il faut corriger et tester ce défaut avant d’intégrer la CLI au runbook normal.

En attendant, ce script Python appelle directement les fonctions de vérification du dépôt. Il a été exécuté avec succès sur une mission synthétique du commit documenté; il utilise des API internes et reste un diagnostic provisoire, pas une interface opérationnelle stable :

```bash
python - <<'PY'
from hold.store import Store
from hold.journal import verify_chain
from hold.replay import journal_profiles, state_replay, verify_decisions

db, mission_id = "hold.db", "<mission>"
store = Store(db)
events = store.events(mission_id)
profiles = journal_profiles(store, events)
chain = verify_chain(events)
profile = profiles[events[0].profile]
state = state_replay(store, mission_id, profile)
decisions = verify_decisions(events, profiles)
print("chain:", chain.valid, "events:", chain.count)
print("state:", state["matches"], state.get("differences"))
print("positions:", decisions["positions"]["matches"])
print("decisions:", decisions["decisions"]["matches"])
print("status:", decisions["status"]["matches"])
PY
```

`hold journal export hold.db <mission> --out mission.json` et `hold journal policy hold.db <mission> --tau 0.3` sont des commandes distinctes. Même une vérification concordante ne prouve pas le registre fournisseur, l’identité externe de l’autorité, l’exhaustivité du journal, l’horloge ni les sorties du modèle. La chaîne détecte certaines altérations mais ne prouve pas la complétude et peut être recalculée par quelqu’un capable de réécrire le stockage entier (`README.md`, `hold/journal.py`).

### Bench expérimental : gel, exécution et re-vérification

Le bench est un instrument expérimental distinct d’une validation d’intégration de production. Vérifier d’abord le protocole figé :

```bash
hold bench check
```

Pour lancer une campagne enregistrée, le code exige `--model` pour A, B, C-llama et D. Le README fournit un exemple d’exécution et l’ordre de reproduction de la campagne :

```bash
ollama pull llama3.2:3b
git checkout e1-a6-frozen
hold bench check
hold bench run --conditions C-llama --model llama3.2:3b --out bench/results/my-c
hold bench reverify --out bench/results/my-c
```

Selon la condition choisie, `--conditions` permet aussi A, B, C, C2, D ou X ; la commande et les critères sont définis dans `hold/cli.py` et `hold/bench.py`. La re-vérification relit les traces brutes sans relancer le modèle et recalcule les éléments disponibles. Pour agréger les résultats, la commande d’évaluation vérifie le fichier de protocole gelé et l’identité stricte des campagnes :

```bash
hold bench evaluate --results bench/results/my-c/results.json bench/results/my-b/results.json bench/results/my-a/results.json
```

L’évaluation peut produire des verdicts inconclusifs si la couverture ou l’identité attendue manque. Le code documente explicitement un écart lors de la re-vérification B : **trois lignes diffèrent sur des champs descriptifs du rapport uniquement ; les champs de verdict restent identiques** (README et erratum E1-A6). Ne pas présenter cette divergence comme inexistante, ni comme un changement des verdicts. Le même seed à température 0 ne garantit pas des résultats identiques sur chaque machine (README). Smoke et probes sont des outils de calibration/diagnostic déclarés « never evidence » dans `hold/cli.py` ; ils ne remplacent pas une campagne d’évaluation.

## 5. Validation indépendante à effectuer avant tout usage réel

Les vérifications du dépôt sont internes au code et à son environnement synthétique. Avant une intégration réelle, un évaluateur indépendant devrait, au minimum :

- vérifier les versions, commit et artefacts du code/protocole, recalculer les traces et les verdicts depuis les sources brutes, documenter les écarts (dont les trois champs descriptifs de B) et examiner la chaîne de conservation des données ;
- auditer les liaisons outil par outil et les chemins réseau/credentials pour s’assurer que HOLD est réellement la seule voie d’exécution, et tester les pannes, timeouts, réponses tardives, ambiguïtés et doublons contre les contrats documentés du fournisseur ;
- établir séparément, par preuve indépendante, le comportement réel d’idempotence du fournisseur avec `operation_id`, la corrélation des reçus et de la recherche de statut, les garanties de non-création en cas de refus et les propriétés de concurrence multi-hôte si elles sont revendiquées ;
- tester que l’adaptateur conserve le contenu, la provenance et les attributs exacts, que les clés/secrets et permissions sont maîtrisés et que chaque résultat ambigu reste non confirmé ;
- valider les contrôles de sécurité, la gestion des profils et signatures, la révocation, l’autorité et son canal, ainsi que la sûreté opérationnelle, la reprise, la sauvegarde et la surveillance dans l’environnement cible ;
- conduire une évaluation contrôlée, préenregistrée et indépendante sur des scénarios, modèles et outils représentatifs différents, avec critères de réussite/échec publics et analyse des résultats négatifs.

## 6. Limites confirmées — à ne pas extrapoler

Le fournisseur de démonstration et de test (`hold/testing.py::SyntheticHotel`, `LedgerHotel`) est **synthétique** ; les effets réels hors du dépôt ne sont pas démontrés. Même lorsque le dépôt contient une implémentation d’envoi WhatsApp, les résultats de campagne et replays sont simulés ; le README indique que le chemin WhatsApp live n’est pas dans la démonstration principale avant validation de ses credentials, flux et preuves d’exécution.

Les résultats E1-A6 portent sur un seul modèle Llama 3.2 3B, une tâche et des scénarios synthétiques. Le protocole est exploratoire et A6 a été conçu après lecture de la campagne précédente sur les mêmes scénarios ; il ne démontre donc pas une garantie universelle. Le README précise que le vérificateur indépendant jugerait actuellement les traces indéterminées. La campagne rapporte aussi deux scénarios accessibles sans mission complétée (S9, S11), et un taux de sollicitations humaines supérieur dans la condition HOLD. En S11, l’approbation du dépassement de plafond est suivie d’un refus `I4/CORRECTIONS` : les demandes invalides antérieures ont consommé le budget de corrections ; le défaut est décrit comme connu, avec correctif prévu après le hackathon, hors protocole gelé. Ces éléments et l’écart descriptif B doivent rester visibles dans toute communication.

HOLD n’affirme pas « exactly once » : il revendique un dispatch au plus une fois uniquement dans la frontière partageant un stockage HOLD ; la déduplication côté fournisseur n’est garantie que si le fournisseur honore l’`operation_id`. Un résultat impossible à confirmer demeure inconnu ; une mission peut se terminer non confirmée. Le journal est vérifiable dans son périmètre, mais ne constitue pas une preuve externe exhaustive. Rien dans ces tests ne démontre une aptitude à la production, une robustesse générale, une conformité réglementaire ou un réentraînement. **Ne promettre ni mise en production ni retrain sur la base de ce dépôt.**

## Références de code principales

`README.md` (résultats et réserves) ; `pyproject.toml` (installation) ; `hold/cli.py` (commandes) ; `hold/profile.py` (schéma et profils) ; `hold/floor.py` (admissibilité, enveloppe, provenance et invariants) ; `hold/runtime.py` (API `Habilitation`, `Mission`, binding et dispatch) ; `hold/outcome.py` (résultats) ; `hold/testing.py` (adaptateur synthétique) ; `hold/bench.py` (protocole et scoring) ; `hold/replay.py` et `hold/journal.py` (rejeu et journal) ; `tests/test_profile_floor.py`, `tests/test_runtime.py`, `tests/test_agent.py`, `tests/test_relay_server.py`, `tests/test_guarantees.py` et `tests/test_bench.py` (couverture testée).
