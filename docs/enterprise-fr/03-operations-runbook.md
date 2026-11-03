# Runbook opérationnel HOLD

**Référence examinée :** dépôt `/tmp/hold-review`, commit `d4ab8acf11f5cea409104753d195370a0769c156`. Ce document décrit l’implémentation présente dans ce commit, pas une offre de support ni une configuration de production certifiée. Le dépôt qualifie HOLD de prototype de recherche (`README.md`, § « Tests ») et utilise un fournisseur synthétique dans ses démonstrations et campagnes.

## 1. Statut de support plateforme

Le projet déclare Python `>=3.10` (`pyproject.toml`, `[project]`), mais ne publie pas de matrice de systèmes d’exploitation pris en charge. Le fonctionnement sur une plateforme donnée est donc **non confirmé** : ne pas interpréter la contrainte Python comme une certification Linux, Windows ou macOS.

Pour le parcours d’installation et de validation décrit ci-dessous, utiliser Linux ou WSL avec Python 3.10 ou supérieur. Windows natif présente une incompatibilité connue dans `hold/testing.py`, qui importe le module Unix `fcntl`. Cela affecte notamment ce module de test et les scénarios qui le chargent ; le dépôt ne documente ni correctif ni prise en charge Windows native. Le contournement pratique est d’exécuter HOLD dans WSL/Linux, non de supposer que chaque chemin Windows natif fonctionne.

Les dépendances de base sont `pydantic>=2.7`, `pyyaml>=6` et `httpx>=0.27`. L’extra `server` installe `fastapi>=0.110` et `uvicorn>=0.29`, et l’extra `dev` installe `pytest>=8` (`pyproject.toml`).

## 2. Bootstrap Linux / WSL

Depuis la racine du dépôt, créer un environnement virtuel, l’activer et installer le projet avec les extras nécessaires. La commande d’installation en mode éditable est documentée dans `README.md` :

```bash
python3 --version                 # vérifier >= 3.10
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[server,dev]"
```

Pour un usage sans serveur ni tests, les dépendances de base seules peuvent être installées avec `python -m pip install -e .`. Le point d’entrée CLI est `hold` (`pyproject.toml`, `[project.scripts]`). Le serveur requiert l’extra `server` ; les tests requièrent `dev`.

Vérifier que le répertoire de données retenu est accessible en écriture et que la sauvegarde y disposera d’un emplacement séparé. La base par défaut du runtime est `hold.db` (`hold/store.py`, `Store.__init__`), donc préférer un chemin explicite et persistant au répertoire de travail implicite.

## 3. Commandes de validation disponibles

### Profil et intégrité du journal

Les formes d’arguments ci-dessous correspondent au parseur `argparse` de `hold/cli.py` et à `hold --help` :

```bash
hold profile validate hold/profiles/reservation.hold.yaml
hold profile fingerprint hold/profiles/reservation.hold.yaml
hold profile schema
hold profile sign hold/profiles/reservation.hold.yaml --secret "$HOLD_PROFILE_SECRET" --by "Nom autorisé" --out /chemin/profile-signe.hold.yaml
hold missions /chemin/hold.db
hold journal export /chemin/hold.db <mission> --out /chemin/mission.json
hold journal policy /chemin/hold.db <mission> --tau 0.3
```

`profile validate` vérifie le profil et affiche empreinte, autorité, seuil, pouvoirs et signature ; `fingerprint` calcule son empreinte ; `schema` imprime le schéma JSON sans fichier ; `sign` exige `--secret` et `--by` et écrit vers `--out` (ou remplace le fichier source si `--out` est omis). Protéger le secret, ne pas le placer dans l’historique de shell en production.

`missions` liste les missions. **La commande `hold journal verify` est connue comme défectueuse dans ce commit.** Elle affiche la chaîne, le rejeu d’état et les décisions, mais sa condition de retour référence `dr["matches"]` sans définir `dr` (`hold/cli.py::_journal_cmd`) : sur une mission valide, elle termine donc en `NameError` et code de sortie non nul. Ce défaut a été reproduit. Ne pas considérer la commande comme un contrôle opérationnel fiable avant correction et test de non-régression. Le document d’intégration fournit un diagnostic API provisoire utilisant `verify_chain`, `state_replay` et `verify_decisions`. Le contrôle Studio retourne séparément chaîne, état et décisions, mais ne remplace pas une vérification indépendante de complétude.

### Tests et vérification fonctionnelle

```bash
python -m pytest
```

ou, conformément au README :

```bash
pytest
```

La suite sous `tests/` couvre notamment le runtime et ses garanties, profils/plancher, rejeu/journal, contrats E1-A6, relay et serveur (`tests/test_runtime.py`, `test_guarantees.py`, `test_relay_server.py`, `test_e1_a6_contract.py`, autres tests). Un test vert ne constitue pas une qualification du fournisseur, du relais WhatsApp réel, d’un système de production ni d’un SLO.

### Bench expérimental — ne pas confondre avec un contrôle de production

`hold bench --help` déclare `freeze`, `check`, `run`, `rerun`, `evaluate`, `smoke`, `probe`, `reverify`. Le README donne les exemples suivants pour reproduire l’expérience, qui dépend d’Ollama, d’un modèle et de données expérimentales :

```bash
hold bench check
hold bench run --conditions C-llama --model llama3.2:3b --out bench/results/my-c
hold bench reverify --out bench/results/my-c
hold bench evaluate --results bench/results/my-c/results.json bench/results/my-b/results.json bench/results/my-a/results.json
```

`run` exige `--model` pour A, B, C-llama et D ; `reverify` vérifie les traces d’un dossier. `smoke` et `probe` sont des diagnostics, explicitement pas des preuves (`hold/cli.py`, traitement de ces actions ; `hold/bench.py`). `freeze` crée un protocole expérimental : ce n’est pas une migration de schéma ni une opération de déploiement. N’exécuter aucune campagne comme si elle validait une disponibilité de service.

## 4. Lancement du serveur

Deux formes de lancement sont effectivement implémentées :

1. **Studio / webhook autonome** :

   ```bash
   hold studio --db /chemin/hold.db --host 127.0.0.1 --port 8787
   ```

   Arguments réels par défaut : `--db hold.db`, `--host 127.0.0.1`, `--port 8787` (`hold/cli.py`, branche `studio`). La commande appelle `serve(..., background=False)` et reste au premier plan. Arrêter avec SIGINT (Ctrl-C) ou le signal d’arrêt géré par Uvicorn. Cette forme ne transmet pas à un runtime les objets d’habilitation ni ses fonctions d’effet : le serveur autonome peut enregistrer/répondre, mais l’agent doit reprendre sa mission, conformément à l’en-tête de `hold/server.py`.

2. **Serveur embarqué dans le processus de l’agent** : appeler `Habilitation.serve(host=..., port=..., background=...)` après avoir construit l’objet `Habilitation` (`hold/runtime.py::Habilitation.serve`). Cet appel nécessite l’extra `server` et permet à la continuation de mission d’être associée au runtime embarqué. Il n’existe pas dans le CLI une commande autonome générique `hold serve`.

`hold demo --serve <port>` est également une option réelle, mais elle démarre le serveur pendant une démo, pas un mode de service autonome recommandé. Ne pas présenter un `uvicorn hold.server:app` comme commande disponible : le serveur expose `create_app(store, habilitations, ...)` et `serve(...)`, pas une variable globale `app` configurée.

### Contrôle de santé et accès

Après démarrage, le contrôle disponible est :

```bash
curl -fsS http://127.0.0.1:8787/api/health
```

`GET /api/health` renvoie `ok: true` et les indicateurs de configuration `whatsapp.send`, `whatsapp.receive` et le mode relay (`hold/server.py::create_app`, route `/api/health`). Ce n’est qu’un contrôle de processus/configuration ; il ne vérifie pas la base, l’accès fournisseur, la livraison d’un message, la correction du journal ou une mission bout-en-bout. Il n’y a pas de route de readiness/liveness distincte ni de métriques de service déclarées.

Le webhook est accessible sur `/api/whatsapp/webhook`. Sa vérification GET exige le jeton de vérification ; son POST exige un secret configuré et une signature `X-Hub-Signature-256` valide. L’API missions du Studio est limitée à loopback sans en-tête de proxy, ou protégée par `HOLD_STUDIO_TOKEN` (`server.py::studio_guard`). Pour tout accès distant, configurer le jeton secret via l’environnement et transmettre `Authorization: Bearer …`; ne pas exposer l’API Studio sans protection. Le dépôt ne fournit pas de terminaison TLS intégrée ni de proxy inverse. L’exemple WhatsApp du README demande un accès HTTPS au webhook, éventuellement via un tunnel, mais ce n’est pas une architecture de production approuvée.

## 5. Base, schéma et migrations

Le stockage est SQLite embarqué, sans serveur de base externe (`hold/store.py::Store`). À l’ouverture, le code active `PRAGMA journal_mode=WAL` hors mémoire, active les clés étrangères et exécute `SCHEMA` contenant des `CREATE TABLE/INDEX IF NOT EXISTS`. L’unicité de `effects.effect_key`, `effects.operation_id` et `relay_messages.wa_message_id`, ainsi que la transaction `BEGIN IMMEDIATE`, portent des protections d’intégrité et de concurrence dans le périmètre de ce store.

Dans le commit examiné, aucun numéro de version de schéma, registre de migrations, commande de migration, procédure de mise à niveau ou test de migration n’est présent. L’exécution répétée de `CREATE ... IF NOT EXISTS` ne constitue pas une migration des tables existantes. **Ne pas supposer une compatibilité ascendante de la base avec une autre version du code.** Avant toute mise à niveau, arrêter les écrivains, faire une sauvegarde cohérente, conserver le binaire/code et les profils correspondant à la base, puis valider le changement sur une copie. Si une modification nécessite une transformation de schéma, la migration est un prérequis d’ingénierie absent de ce dépôt.

La base en mode WAL peut avoir des fichiers associés `-wal` et `-shm`. Ne pas copier uniquement le fichier principal pendant que le processus écrit. Utiliser une sauvegarde SQLite cohérente à l’aide de l’API de sauvegarde SQLite ou arrêter proprement tous les processus écrivains avant copie ; conserver la base et ses fichiers WAL associés si la copie se fait hors ligne. Le dépôt ne fournit aucune commande automatisée de sauvegarde ou de restauration.

## 6. Journalisation et surveillance

HOLD écrit dans SQLite les missions, profils, événements chaînés, effets, requêtes d’autorité et enregistrements relay (`hold/store.py::SCHEMA`, `Store.append`; `hold/journal.py`). Les événements et la projection d’état sont écrits dans une transaction. Pour une mission donnée, collecter périodiquement l’état avec `hold missions <db>` et inspecter la mission dans Studio si disponible ; exporter les éléments nécessaires avec `hold journal export <db> <mission> --out <fichier>`. Protéger exports et base comme données sensibles : le journal peut contenir des données de mission, d’action et de preuve.

Surveiller au minimum, par un mécanisme d’exploitation extérieur à ce dépôt : disponibilité du processus et de SQLite, espace disque, erreurs de requêtes, durée d’indisponibilité du relais, missions `HELD` et statuts `CLOSED_UNCONFIRMED`, effets `UNKNOWN`/`DISPATCHING`, messages relay rejetés/échoués, et résultats des vérifications de journal. Le dépôt ne définit ni format de logs centralisés, ni rotation/rétention, alertes, tableaux de bord, métriques, SLO, SLA ou astreinte. Les logs Uvicorn sont configurés au niveau `warning` dans `server.py::serve`; ne pas compter sur ce seul flux comme audit complet.

L’empreinte chaînée détecte une altération partielle, mais ne prouve pas que l’historique est complet ; une personne capable de réécrire toute la base peut recalculer la chaîne (`README.md`, § garanties). Les contrôles de rejeu ne vérifient pas le registre fournisseur, l’identité réelle de l’autorité, les sorties du modèle, l’exhaustivité ou l’horloge (`hold/cli.py::_journal_cmd`). Conserver, si nécessaire, un témoin externe indépendant ; le dépôt ne l’implémente pas.

## 7. Sauvegarde et restauration

**Sauvegarde avant chaque changement de code/profil ou opération risquée :**

1. Suspendre les nouveaux appels d’agents et confirmer qu’aucun écrivain ne poursuit une transaction.
2. Réaliser une sauvegarde cohérente SQLite (API de sauvegarde), ou arrêter tous les processus utilisant la base puis copier la base hors ligne avec son état WAL complet. Enregistrer date, version/commit HOLD, empreinte du profil et emplacement de sauvegarde dans un registre opératoire distinct.
3. Chiffrer et contrôler l’accès à la sauvegarde ; ne pas inclure de secrets WhatsApp dans les exports.
4. Tester périodiquement la restauration sur un emplacement isolé : ouvrir une copie, lister les missions, vérifier les missions critiques, comparer avec les exports/témoins disponibles. Le dépôt ne fournit pas de procédure automatique de restauration.

**Restauration :** arrêter l’ensemble des processus HOLD/agents qui peuvent écrire, préserver une copie forensique de l’état défaillant, restaurer un instantané cohérent vers un nouvel emplacement, appliquer le même commit/code et profils que lors de la sauvegarde, vérifier la chaîne et le rejeu de chaque mission pertinente, puis seulement autoriser une reprise. Une restauration fait perdre tout événement survenu après le point de sauvegarde. Réconcilier auprès du fournisseur les effets externes depuis ce point avant toute nouvelle action : l’état restauré seul ne prouve pas qu’aucun effet n’a été exécuté.

## 8. Traitement des incidents

### Issue d’effet inconnue (`UNKNOWN` / `DISPATCHING`)

Une expiration de délai, erreur de connexion ou erreur système connue lors d’un effet peut conduire à `UNKNOWN` et à l’état de mission `HELD` (`hold/runtime.py`, `UNKNOWN_EXCEPTIONS`, dispatch d’effet). **Ne pas relancer manuellement l’effet** et ne pas déclarer la mission terminée sur la seule parole de l’agent. HOLD bloque l’effet équivalent et les actions dépendantes ; il tente une vérification de statut, un contact support puis, si nécessaire selon le profil, une décision de l’autorité. Une preuve de reçu corrélée à `operation_id` peut permettre la réconciliation et la reprise ; une absence momentanée n’est pas une preuve d’échec. Si l’effet reste introuvable, laisser en attente ou clore explicitement non confirmé selon le profil et la décision autorisée. Le journal est la référence pour ce que HOLD a enregistré, pas le registre du fournisseur.

La protection contre la répétition s’applique aux runtimes qui partagent un même store, sur un hôte ; elle ne couvre pas plusieurs stores/hôtes. La déduplication côté fournisseur n’est garantie que si le fournisseur respecte `operation_id` comme clé d’idempotence. Le runtime enregistre `NOT_DECLARED`, `DECLARED` ou `VERIFIED` selon le binding (`hold/runtime.py::effect_guarantees`). Ne jamais qualifier cela d’« exactly once ».

### Garde anti-doublon déclenchée

Une action équivalente refusée par la règle `I1` est une barrière de sécurité attendue : préserver le refus, l’identifiant de mission et l’effet existant ; examiner `hold missions`, les effets et le journal ; rechercher l’opération correspondante chez le fournisseur par son identifiant avant toute suite. Ne pas contourner la garde en créant une mission/base neuve ou en changeant les arguments pour forcer une seconde exécution. Si la mission concerne un effet déjà confirmé, reprendre le parcours dépendant uniquement avec la preuve adéquate.

### Échec d’intégrité du journal

Suspendre immédiatement les écritures et les appels d’effet pour la base concernée ; isoler une copie en lecture seule et préserver les fichiers de base/WAL. Ne pas réparer, supprimer ou recalculer les empreintes sur l’unique copie. **La commande `hold journal verify` termine actuellement par `NameError` après son rapport ; ne pas la considérer comme contrôle fiable.** Utiliser le script API provisoire du guide d’intégration sur une copie isolée, puis conserver les résultats et les fichiers. Vérifier aussi les autres missions et comparer à un export ou témoin externe. Une chaîne valide n’établit pas la complétude. Reprendre uniquement après compréhension de l’écart, restauration/migration approuvée et vérification indépendante.

### Relay indisponible ou webhook non reçu

Le relais WhatsApp est optionnel ; l’extra serveur, les variables et la configuration Meta doivent être présents. `GET /api/health` renseigne seulement si les champs de configuration autorisent envoi/réception (`WhatsAppConfig.can_send/can_receive`). Les secrets attendus sont `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_APP_SECRET`, `WHATSAPP_VERIFY_TOKEN` ; un modèle approuvé hors de la fenêtre de 24 heures peut être défini avec `WHATSAPP_TEMPLATE_NAME` et `WHATSAPP_TEMPLATE_LANG` (`hold/relay/whatsapp.py`).

En cas d’échec, vérifier l’état du processus, la route HTTPS et la signature Meta, les statuts/errors relay de la mission et la connectivité/API. La réponse entrante est liée à l’identifiant de mission, à la question ouverte et au numéro de l’autorité ; un message du mauvais expéditeur est rejeté. Les identifiants de message Meta répétés sont dédupliqués. Ne pas accepter une décision par canal non authentifié. Si le relais reste indisponible, la réponse console est disponible lorsque l’application est accessible localement : `hold answer <db> <mission> <question_id> <CODE>`. N’utiliser que les codes proposés dans la Decision Request active et authentifier séparément l’opérateur. Une réponse reçue en retard peut être périmée ou rejetée ; relire le statut de mission au lieu de réessayer à l’aveugle.

## 9. Arrêt et retour arrière

Pour l’arrêt planifié, arrêter d’abord l’admission de nouveaux travaux dans l’agent appelant ; laisser les opérations en cours atteindre un état sûr ou consigner explicitement les effets inconnus ; arrêter proprement le processus serveur (Ctrl-C/SIGINT pour `hold studio`) ; confirmer l’arrêt de tous les runtimes utilisant le fichier SQLite avant toute copie ou remplacement. Les threads de continuation runtime sont créés en arrière-plan daemon (`runtime.py::_spawn`) : le dépôt ne définit pas de protocole de drainage durable ni de commande de shutdown applicative. Ne pas tuer le processus en plein effet si une issue peut encore être inconnue.

Pour revenir à une version précédente, arrêter les écrivains, sauvegarder l’état actuel, remettre ensemble le code, le profil et la base compatibles connus. Comme aucune migration/version de schéma n’est fournie, ne pas rétrograder le code sur une base modifiée sans test préalable sur copie. Si restauration de base nécessaire, suivre la procédure de restauration et réconcilier tous les effets externes apparus après le point restauré. Aucun script de rollback n’est fourni.

## 10. Prérequis de production absents

Le dépôt ne démontre pas une aptitude à la production ni ne fournit : matrice de support OS, qualification du relais WhatsApp réel, SLA/SLO, stratégie de haute disponibilité ou réplication, déploiement multi-hôte (les garanties d’anti-duplication ne s’étendent pas à des stores séparés), migrations versionnées, sauvegardes/restaurations automatisées et testées, rotation/rétention de logs, métriques et alertes, gestion de secrets/rotation, gestion de clés et témoin externe inviolable, procédure de reprise après sinistre, contrôle d’accès administrateur complet, TLS/proxy durci ou cycle de support/astreinte. L’API Studio est limitée par loopback ou jeton, mais le dépôt ne constitue pas à lui seul une solution complète de sécurité réseau.

Avant une décision de production, prévoir au minimum une revue de sécurité et de confidentialité, des migrations et essais de restauration, qualification des intégrations réelles et de leurs garanties d’idempotence, contrôles indépendants de l’audit, observabilité et procédures d’incident/astreinte. Aucun objectif de disponibilité ou délai de rétablissement n’est spécifié ici.
