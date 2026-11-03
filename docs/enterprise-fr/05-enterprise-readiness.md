# HOLD — seuils de préparation à l’entreprise

**Basé sur :** HOLD `main` commit `d4ab8acf11f5cea409104753d195370a0769c156`, examiné le 3 novembre 2026. Cette liste n’est ni une certification ni un avis réglementaire. « Absent » signifie non fourni ou non démontré dans cette révision du dépôt.

## Décision actuelle

**Ne pas utiliser cette révision pour déclencher des effets réels de production.** Le prototype démontre des contrôles logiciels utiles dans un périmètre local à stockage partagé et dans des scénarios synthétiques. Il n’apporte pas encore les mécanismes d’exploitation, d’identité, de résistance aux altérations et d’intégration fournisseur nécessaires à un déploiement d’entreprise. En outre, la commande CLI de vérification du journal est actuellement défectueuse et doit être corrigée avant d’être incluse dans un processus de contrôle.

**Mise à jour de périmètre :** ce document décrit l’ancien commit `d4ab8acf`. Sur le `main` courant `b94a1e9`, `hold/studio/index.html` est présent et la suite observée compte 167 tests réussis; le défaut `dr` de `hold journal verify` demeure. Un ZIP additionnel Experience v1 contient un prototype d’export `hold.experience-envelope/v0` et passe cinq tests ciblés dans un clone scratch du HEAD, mais il n’est ni publié ni fusionné. Voir le [registre des affirmations et preuves](06-claims-evidence-register.md) pour les détails, les tests combinés et les limites.

## Porte 0 — fiabilité des vérifications du code

Avant toute qualification, la version candidat doit satisfaire ces critères :

- Corriger la variable `dr` non définie dans `hold/cli.py::_journal_cmd`; ajouter un test CLI de non-régression qui exécute `hold journal verify` sur une mission valide et invalide, vérifie les codes de sortie, et détecte les erreurs d’intégrité.
- Exécuter toutes les suites sur les plateformes d’exécution revendiquées et publier le commit, les dépendances verrouillées et les rapports de résultats. Le dépôt n’a pas de matrice OS; `fcntl` bloque certaines importations sous Windows natif.
- Ajouter une version explicite du schéma de base et des migrations testées, ainsi qu’un chemin documenté de mise à niveau et de retour arrière.
- Pour le commit `d4ab8acf` décrit à l’origine ici, vérifier la disponibilité des ressources Studio au packaging/exécution : le code cherchait `studio/index.html`, alors que ce checkout incluait `studio/studio.src.html` et `studio/core.js`. Le `main` actuel contient désormais `hold/studio/index.html`; le packaging et le déploiement restent à tester.

## Porte 1 — intégrité des effets fournisseur

Aucun effet réel ne doit être autorisé avant que l’équipe d’intégration ait démontré les points suivants avec le système cible :

- Tous les chemins d’exécution qui possèdent des credentials passent par HOLD; l’agent ne reçoit pas de credentials utilisables directement et aucun outil parallèle ne contourne le plancher.
- Contrat de statut documenté pour chaque action d’effet : sémantique de refus, acceptation, timeout, création et réponse perdue, reçu, recherche de statut, visibilité retardée, échec prouvé, erreur temporaire et état impossible à déterminer.
- Garantie d’idempotence fournisseur vérifiée avec `operation_id`, ou conception compensatoire/reconciliation validée par le fournisseur; tests de rejeu de requêtes, crash après réservation, crash après acceptation fournisseur, et reprise après restauration de base.
- Essais de concurrence sur tous les processus et hôtes possibles. Le code ne coordonne pas les bases distinctes; un stockage SQLite local partagé ne constitue pas un coordinateur multi-région.
- Preuve de corrélation indépendante des reçus et de leur autorité : un message libre de support ou une sortie de l’agent n’est pas un reçu.
- Conception et critères d’arrêt explicites pour les cas `UNKNOWN`, `HELD`, `CLOSED_UNCONFIRMED`, statut absent et état fournisseur discordant. Jamais d’interprétation de timeout comme preuve de non-exécution.

## Porte 2 — identité, autorisation et sécurité de service

Avant d’exposer le service au réseau ou à des opérations sensibles :

- Authentifier l’agent, l’opérateur, les tâches et chaque appel qui traverse le runtime; limiter l’autorisation par principal, mission et tenant. Le jeton Studio unique ne fournit pas de RBAC ou de séparation d’utilisateurs.
- Définir l’identité de l’autorité et la preuve de son pouvoir d’approuver; évaluer la compromission du téléphone, du compte WhatsApp, du numéro et les comptes partagés. Vérifier le délai de révocation et son comportement lors d’un effet en vol.
- Rendre obligatoire selon une politique versionnée la validation/signature des profils; gérer les clés dans un coffre, avec rotation, révocation, séparation des tâches et procédure de compromission. La signature HMAC disponible est optionnelle et nécessite le secret partagé.
- Exposer uniquement les routes nécessaires derrière TLS, proxy de confiance, filtrage réseau, rate limiting, limite de taille, gestion d’erreurs et contrôle d’accès revu; séparer autant que possible webhook public et Studio d’administration.
- Conduire revue de code et dépendances, inventaire SBOM, analyse des vulnérabilités, tests d’intrusion indépendants, gestion des secrets, politique de correctifs et processus de réponse aux incidents.
- Établir classification des données, chiffrement des volumes et sauvegardes, rétention/suppression, contrôle d’accès et journalisation des consultations. Le journal peut contenir des données personnelles et métier.

## Porte 3 — exploitation et continuité

Le propriétaire de service doit définir avant un pilote réel :

- Unité de déploiement et topologie supportées, gestion de processus, arrêt/drain, supervision, alertes et on-call. Le dépôt ne livre ni orchestration, ni stratégie de haute disponibilité, ni SLO/SLA.
- Sauvegarde SQLite cohérente incluant la gestion du WAL, copie hors hôte, chiffrement, rétention et exercices de restauration. Définir RPO/RTO, et procédure de réconciliation de tous les effets fournisseurs après restauration à un point antérieur.
- Capacité, espace disque, rotation, rétention et observabilité du journal, du service et du relay. L’endpoint `/api/health` n’est pas une vérification bout-en-bout du fournisseur ou de la correction de la base.
- Runbooks approuvés pour incertitude d’effet, doublon refusé, incident journal, indisponibilité du relay, fuite de secret, révocation et rollback. Tester chaque procédure en exercice isolé.
- Gestion de versions et migrations compatibles; ne pas redémarrer une version nouvelle ou ancienne sur une base sans essai explicite de compatibilité.

## Porte 4 — preuve d’efficacité au-delà du prototype

Avant de publier une revendication de performance ou de réduction de risque :

- Évaluation indépendante préenregistrée sur de nouveaux scénarios non utilisés pour concevoir le mécanisme, plusieurs modèles et configurations, outils/fournisseurs réalistes, charge, retards, erreurs et comportements adversariaux.
- Séparer taux d’action bloquée par le plancher, décisions de discernement, mission achevée avec preuve, demandes humaines, faux refus et effets externes réellement exécutés.
- Définir dénominateurs, règles de scoring et traitement des données manquantes; faire vérifier indépendamment les traces, les champs de rapport et les verdicts.
- Résoudre ou republier explicitement les divergences descriptives de revalidation B S11. L’erratum courant indique que trois champs descriptifs divergent tandis que les champs de verdict H1/H2 restent identiques; cela doit rester visible.
- Inclure les défauts connus dans la décision de release. En E1-A6, l’approbation d’un dépassement de plafond peut échouer ensuite sur le budget de corrections S11.

## Contrôles de conception à ne pas enlever

Les priorités de production ne doivent pas affaiblir le modèle de sécurité existant : refus avant appel; preuve explicite d’un résultat; non-répétition après résultat inconnu; réservation transactionnelle avant effet; liaison d’approbation aux attributs, révision et expiration; contrôle indépendant des propositions du discerner; et distinction entre événement vérifiable et preuve externe. Toute modification de ces mécanismes exige des tests de non-régression et une revue d’impact fournisseur.

## Revue / approbation requise

Avant de franchir une porte, consigner le responsable, l’évidence, la date, le commit et les exceptions acceptées. La porte d’effet réel exige l’approbation conjointe des propriétaires produit/métier, sécurité, intégration fournisseur, exploitation et responsable de la donnée. Cette documentation ne présume pas que ces approbations ont été obtenues.
