# Modèle de menace — HOLD

**Statut :** analyse documentaire du dépôt au commit `d4ab8acf11f5cea409104753d195370a0769c156` (branche `main`), et non audit de sécurité ni certification. Les contrôles décrits sont ceux visibles dans le code et les tests examinés; ils ne constituent pas une garantie de sécurité du système complet.

## Résumé exécutif

HOLD est une couche de contrôle embarquée pour l’exécution d’actions par un agent. Le runtime soumet les appels à un plancher de règles piloté par un profil, inscrit les décisions et résultats dans SQLite et peut demander une décision à une autorité par WhatsApp. Le code met notamment en œuvre une réservation transactionnelle des effets équivalents, un état `UNKNOWN` en cas de résultat incertain, une corrélation des preuves avec un identifiant d’opération, des validations de profil et un webhook entrant authentifié par HMAC.

Ces contrôles s’appliquent aux chemins effectivement traversant HOLD et à un magasin partagé donné. Ils ne protègent pas contre un chemin d’exécution parallèle qui détient directement les identifiants du fournisseur, un administrateur capable de réécrire la base entière, une compromission du processus ou des secrets, ni les effets secondaires qui échappent à la détection et à la corrélation configurées. Le dépôt qualifie le projet de prototype de recherche; le README indique que l’intégration réelle du relay n’est pas validée dans la démonstration principale, qui s’appuie sur un fournisseur synthétique. Aucune certification ou validation de sécurité indépendante n’est revendiquée.

## Périmètre et hypothèses

Cette analyse couvre les composants ciblés : `hold/server.py`, `hold/relay/whatsapp.py`, `hold/runtime.py`, `hold/floor.py`, `hold/store.py`, `hold/journal.py`, `hold/profile.py`, les tests associés et `README.md`. Elle examine le serveur HTTP facultatif, le relay WhatsApp Cloud/simulé, l’exécution des outils par le runtime, la persistance SQLite, les profils et l’autorité humaine.

Hors périmètre : audit complet des dépendances et de la chaîne de livraison, du système d’exploitation et de l’hôte, de Meta/WhatsApp, des API réelles des fournisseurs, de tous les agents/discerners, des interfaces Studio côté navigateur, de la configuration de tunnel/TLS ou de toute installation particulière. Le dépôt ne permet pas de conclure à leur configuration effective. Les exemples du README précisent que le fournisseur est synthétique dans le dépôt et que l’intégration réelle WhatsApp n’est pas la démonstration principale.

Hypothèse de sûreté centrale du README : HOLD doit être le seul détenteur des identifiants permettant les effets chez le fournisseur. Si un agent, un opérateur, un autre service ou une seconde installation peut agir directement, les règles du plancher ne couvrent pas cette voie. La garantie d’« au plus une fois » est explicitement limitée à l’exécution dans la frontière d’un même magasin HOLD, et non à plusieurs magasins ou hôtes.

## Actifs à protéger

- **Pouvoirs d’action et limites** : actions autorisées, champs/provenance, plafonds, budgets, échéances, fenêtres temporelles et conditions inscrits dans les profils (`hold/profile.py`, `hold/floor.py`).
- **Effets externes** : création, paiement, envoi ou autre effet exécuté par les fonctions d’outil enregistrées dans `Habilitation.effect()` (`hold/runtime.py`).
- **État de mission** : réservation d’effets, statut, identifiant d’opération, résultat, requêtes d’approbation, approbations à usage unique et révocation (`hold/store.py`; `Mission.dispatch()`, `Mission.answer()`, `Mission.revoke()` dans `hold/runtime.py`).
- **Preuves et historique** : reçus, événements, liens causaux, profils attachés, décisions et métadonnées de relais (`hold/journal.py`, `hold/store.py`, `hold/replay.py`).
- **Autorité et canal de réponse** : numéro/contact et options de décision, ainsi que la capacité de révoquer une mission (`Authority` dans `hold/profile.py`; `Inbound` dans `hold/relay/whatsapp.py`).
- **Secrets de service** : jeton Graph API, secret de signature webhook, jeton de vérification, jeton de Studio et secret optionnel de signature de profil (`WhatsAppConfig.from_env()`, `create_app()`, `sign()` dans les modules correspondants).
- **Données personnelles et métier** : titres, attributs d’actions, profils, questions, numéros de téléphone, messages entrants/sortants, reçus et traces conservés dans SQLite ou transmis à Meta et aux outils externes.

## Frontières de confiance et flux

1. **Agent/discerneur → runtime HOLD.** L’agent formule des propositions et peut recevoir descriptions, schémas et résultats d’outils. Le runtime enregistre des fonctions associées aux pouvoirs du profil (`Habilitation._register()`, `read()`, `effect()`, `status()`, `contact()`). La sûreté dépend du fait que l’appel d’effet réel soit bien encapsulé par `Mission.dispatch()` et que l’agent ne dispose pas d’une voie parallèle vers le fournisseur.
2. **Runtime → plancher.** `admit_action()` dans `hold/floor.py` vérifie notamment pouvoir, enveloppe, budget, expiration, révocation, provenance et état antérieur. Dans `Mission.dispatch()`, la réservation de l’effet et l’événement de journal correspondant sont écrits avant l’appel à la fonction d’outil; l’effet en vol/ambigu/connu n’est pas répété sous la même identité de mission/action/propriétés.
3. **HOLD → fournisseur/outils.** Les fonctions fournies par l’intégrateur font les appels réseau ou autres effets. HOLD transmet un `operation_id`; la déduplication côté fournisseur n’est garantie que si celui-ci le respecte. La métadonnée `idempotency=
operation_id"` est une déclaration de l’intégrateur; `effect_guarantees()` distingue déclaration et vérification, mais HOLD ne vérifie pas lui-même le contrat du fournisseur.
4. **HOLD ↔ autorité via WhatsApp.** Les demandes sortantes vont au contact configuré; les réponses entrantes sont acceptées via webhook signé et liées à mission/question/contact. Le webhook est exposé publiquement par conception, alors que les API de mission du Studio sont restreintes dans `studio_guard()` (`hold/server.py`).
5. **Client opérateur → Studio API.** Les routes de catalogue/validation sont publiques dans le code; les routes des missions, détails et réponses console sont protégées par un jeton Bearer ou l’accès loopback sans en-tête de transfert (`create_app()`).
6. **Processus HOLD ↔ SQLite.** `Store` utilise SQLite, transactions `BEGIN IMMEDIATE`, WAL sauf `:memory:`, et écrit en transaction événements, projections et réservation. Le fichier et ses journaux sont une frontière de confiance locale; aucun chiffrement, contrôle d’accès applicatif par utilisateur ou sauvegarde distante n’est établi ici.
7. **HOLD → Meta/Internet.** L’envoi Cloud utilise `httpx` vers Graph API en HTTPS par défaut; `api_base` est configurable, ce qui rend sa valeur effective critique.

## Menaces et scénarios d’abus

**Agent malveillant, compromis ou hallucinant.** Il peut proposer une action hors périmètre, réutiliser des valeurs inventées ou déclarer un succès fictif. L’impact peut être un effet non autorisé, une mission incohérente ou la tromperie d’un opérateur. Le plancher vérifie l’action, l’enveloppe et la provenance configurées; un effet incertain n’est pas répété et le succès exige une preuve corrélée. Cela ne garantit ni l’exhaustivité des règles métier, ni l’honnêteté des outils, ni l’absence d’influence du modèle sur l’utilisateur. Le README rapporte des affirmations de succès non prouvées par l’agent pendant les essais.

**Contournement du runtime.** Un chemin direct ou un autre processus peut produire un effet sans passer par le profil. Le dépôt n’empêche pas le détenteur de credentials d’appeler directement le fournisseur. Les credentials et fonctions d’effet doivent donc rester derrière HOLD.

**Panne, timeout ou réponse perdue après acceptation fournisseur.** Un retry peut dupliquer l’effet; l’absence de preuve peut aussi laisser la mission non réconciliée. Le runtime utilise `DISPATCHING`/`UNKNOWN`, des vérifications de statut/support, un reçu lié à `operation_id` et une réservation `effect_key` unique. La déduplication fournisseur ne tient que si ce dernier respecte le contrat annoncé; des magasins/hôtes distincts ne sont pas coordonnés.

**Compromission ou rejeu du webhook.** Une approbation ou révocation frauduleuse/répétée est possible si le canal ou ses secrets sont compromis. Le code vérifie HMAC-SHA256 sur le corps brut par `verify_signature()` avec comparaison constante, unicité de `wa_message_id` et validation du contact, mission, demande et options. La protection dépend de la confidentialité de `app_secret`, de l’identité du numéro fournie par Meta et de la configuration TLS. Le dépôt ne montre pas de protection contre compromission du compte/téléphone ou divulgation du secret, ni de politique générale d’expiration temporelle des anciens webhooks signés.

**Usurpation ou perte du téléphone de l’autorité; erreur de profil/contact.** Une personne incorrecte peut prendre une décision ou une révocation peut être manquée. Les questions et approbations sont liées à `authority.contact`, au profil, à une action/attributs, à une expiration et à un usage. L’identité s’appuie toutefois sur le numéro WhatsApp et le profil; aucune authentification multifacteur ni validation indépendante de l’identité humaine n’est montrée.

**Vol d’un jeton Studio ou exposition réseau accidentelle.** Cela peut permettre lecture des missions/journaux ou réponses console non autorisées. `studio_guard()` refuse les requêtes hors loopback sans Bearer valide et compare le jeton en temps constant. Le dépôt n’établit pas de gestion multi-utilisateur/roles, rotation ou stockage sécurisé des jetons. Proxy, en-têtes, configuration ou journalisation peuvent affaiblir la frontière.

**Réécriture, suppression, restauration sélective ou corruption de base.** Les preuves, approbations, révocations et état anti-doublon peuvent être altérés ou supprimés. La chaîne, les relectures, transactions et contraintes SQLite détectent certaines incohérences; le chaînage seul ne prouve ni complétude ni résistance à une réécriture totale, et un acteur capable de réécrire la base peut recalculer la chaîne. Aucun témoin externe immuable ou mécanisme de sauvegarde n’est démontré dans ces composants.

**Panne disque, saturation, corruption ou restauration d’une ancienne sauvegarde.** Le journal ou l’état de réservation peut être perdu et un effet répété. SQLite/WAL contribue à la cohérence locale, mais aucune stratégie de sauvegarde, restauration, réplication, rétention, alerte disque ou exercice de reprise n’est spécifiée. Une copie ancienne peut oublier un effet déjà exécuté chez le fournisseur.

**Fuite ou accès abusif aux données.** Numéros, intentions, reçus et opérations métier peuvent être exposés. Certains numéros sont masqués dans les traces relay, mais les données complètes persistent dans d’autres enregistrements. Le dépôt ne démontre pas de chiffrement au repos, minimisation/rétention, anonymisation ou contrôle par mission.

**Partage multi-tenant mal configuré.** Un utilisateur peut consulter ou influencer les missions d’un autre tenant. Les identifiants de mission segmentent des requêtes internes, mais Store/Studio ne montrent pas de modèle tenant, principal authentifié ou autorisation par tenant. Le jeton protégé donne une vue globale des missions; ne pas considérer HOLD comme isolant des tenants sans couche supplémentaire.

**Erreur réseau, proxy ou URL API.** Studio peut être exposé ou un jeton WhatsApp transmis à un endpoint contrôlé. Le serveur se lie par défaut à `127.0.0.1`, valide certains en-têtes forwarding, et l’API Graph utilise HTTPS par défaut. `serve(host=...)` et `api_base` sont configurables. Le code examiné n’établit ni TLS entrant Uvicorn, ni limitation de débit, quotas, limite de taille, WAF ou politique réseau sortante.

**Déni de service ou surcharge.** API/webhook peuvent devenir indisponibles et SQLite saturer ou croître sans contrôle. Le parsing est défensif et le timeout HTTP sortant vaut 10 secondes; aucun mécanisme général de quota, limitation de taille/débit, rétention ou supervision de capacité n’est établi dans les fichiers examinés.

**Révocation tardive ou mission déjà en vol.** Une action peut être partie malgré STOP/révocation, et les effets antérieurs ne sont pas annulés. Le statut révoqué bloque les nouvelles actions admises; STOP traite les missions actives pour le contact autorisé. La révocation dépend de la réception et du traitement du webhook et ne peut annuler un effet déjà remis au fournisseur. L’état externe peut donc diverger de celui de la mission.

## Contrôles existants observés

### Contrôle d’action et d’autorité

- Le profil décrit les pouvoirs, enveloppes, budgets, autorité et expiration. Les profils peuvent avoir empreinte, révision/supersession et signature HMAC (`Profile`, `sign()`, `signature_valid()`, `load_profile()` dans `hold/profile.py`). Le chargement vérifie la signature seulement quand `require_signature=True` ou qu’un secret est fourni avec une signature; aucune politique universelle d’obligation de signature n’est implicite.
- Le plancher applique les règles non configurables I1–I5 et la provenance I6 (`hold/floor.py`, `admit_action()`, `provenance_match()`). Les tests `tests/test_profile_floor.py`, `tests/test_runtime.py`, `tests/test_e1_a5_recovery.py` et `tests/test_authority_binding.py` couvrent plusieurs refus et scénarios.
- Les approbations sont associées à des attributs exacts, action, empreinte/révision de profil, expiration et usage unique (tests `tests/test_authority_binding.py`); changement de profil invalide les demandes en cours, et l’adoption est explicitement journalisée (`Habilitation.mission(..., adopt=True)`).
- Le runtime réserve les effets avant exécution, refuse les effets équivalents déjà confirmés/en cours/inconnus et évite d’annoncer un succès sans preuve. La revendication de prévention côté fournisseur reste conditionnelle.

### Webhook, relay et serveur

- Vérification du webhook entrant avec HMAC SHA-256 sur le corps brut et comparaison constante; rejet si secret/en-tête absent ou format invalide (`verify_signature()`, `wa_event()`; tests `tests/test_relay_server.py`).
- Validation du format du bouton, numéro d’autorité, mission, demande ouverte et option; déduplication du message par identifiant unique en base; STOP/revoke reconnu seulement pour le contact autorisé (`Inbound.handle()`, `_seen()`, tests relay).
- Endpoint de vérification initiale du webhook protégé par `verify_token`; endpoint POST indisponible sans `app_secret`; mode simulé explicitement disponible si pas de configuration d’envoi (`WhatsAppConfig`, `WhatsAppRelay`).
- Studio API : loopback ou Bearer `HOLD_STUDIO_TOKEN`, vérification constante; `/api/health`, catalogue et validation des profils ne révèlent pas les missions selon le code. `tests/test_relay_server.py` teste la garde locale/token.

### Persistance et intégrité

- SQLite utilise transaction exclusive d’écriture (`BEGIN IMMEDIATE`) et WAL pour les fichiers; contraintes d’unicité sur `effect_key`, `operation_id`, identifiant de requête et identifiant de message WhatsApp (`hold/store.py`). Réservation, événement et projections sont transactionnels dans le chemin normal de dispatch.
- Les événements sont ordonnés, chaînés à l’événement précédent et encodés par enveloppe/version (`hold/journal.py`); vérification de chaîne et relecture état/décisions exposées via mission et Studio. Les tests incluent altération simple et réécriture avec recalcul pour démontrer que la chaîne seule n’est pas un ancrage externe (`tests/test_studio_parity.py`, `tests/test_showcase.py`).
- Les profils attachés sont stockés par empreinte et leur contenu est journalisé/attaché aux missions; un changement de profil est consigné.

## Contrôles absents, optionnels ou inconnus

Ce qui suit n’est pas établi par les composants examinés; il ne faut pas supposer son existence dans un déploiement :

- signature obligatoire et gestion de cycle de vie des clés de profil; stockage, rotation, révocation et séparation des secrets; chiffrement des secrets au repos; coffre de secrets;
- authentification des opérateurs du Studio, comptes nominatifs, moindre privilège, RBAC, séparation des responsabilités, audit des consultations/exportations;
- isolation multi-tenant, autorisation objet/mission, limites de périmètre sur la base et sur les profils résolus;
- chiffrement de la base et des sauvegardes, politique de rétention/suppression, sauvegarde cohérente du fichier SQLite avec WAL, copies hors site, immutabilité, restauration périodiquement testée, continuité d’activité;
- ancrage externe du journal (témoin indépendant, signature périodique, stockage append-only/WORM), preuve de complétude ou détection de troncature par tiers;
- validation indépendante de reçus auprès du fournisseur, garantie fournisseur d’idempotence, couverture de tous les chemins de l’effet, gestion des courses entre révocation et appel externe;
- sécurité de production du serveur au-delà du contrôle d’accès décrit : TLS entrant intégré, filtrage IP/réseau, proxy de confiance configuré, limitation de débit/taille, anti-DoS, supervision et alertes;
- inventaire de menaces de dépendances, SBOM, scans, tests d’intrusion, revue de code indépendante, gestion de vulnérabilités ou certification.

L’absence d’information dans ce périmètre ne prouve pas que ces contrôles n’existent dans aucune installation; elle signifie qu’ils ne peuvent pas être crédités sur la seule base de ce dépôt.

## Identité, autorité et révocation

L’autorité est définie par le profil, avec un contact (numéro pour WhatsApp), un canal, une locale et une capacité de révocation. `Inbound` compare l’expéditeur à `authority.contact`; l’acceptation dépend également d’une question ouverte, d’un bouton et d’un code offert. Les demandes sont donc liées à un canal/contact et à une mission, mais le code ne prouve pas l’identité civile du détenteur du numéro. La compromission SIM/appareil/compte WhatsApp, une erreur de saisie dans le profil ou un compte partagé peuvent donner une autorité de fait au mauvais acteur.

STOP (et variantes) révoque les missions actives dont l’autorité correspond et peut révoquer (`Inbound._text()`); `Mission.revoke()` journalise l’action et les effets ultérieurs sont refusés. La révocation est un contrôle futur, pas une annulation d’effets déjà envoyés ni une preuve que la révocation a été reçue à temps. Les protections d’approbation à usage unique, d’expiration et de révision limitent la portée d’un accord, mais ne remplacent pas une identité forte.

## Authentification et autorisation

- **Webhook** : authentification du corps par secret partagé HMAC; jeton distinct pour le challenge d’abonnement. Le mécanisme authentifie le producteur possédant le secret, pas l’auteur humain au-delà des champs de numéro fournis par le webhook.
- **Studio** : accès soit loopback sans en-tête de transfert, soit Bearer `HOLD_STUDIO_TOKEN`. Un jeton unique autorise l’ensemble des routes de mission; pas d’utilisateur/role ni de permission par objet visibles. Le jugement de loopback dépend du client et des en-têtes de proxy reçus.
- **Agent et outils** : aucune identité cryptographique d’agent ou authentification individuelle des appels d’outils n’est établie dans ces composants. Le profil contrôle les capacités proposées au runtime; l’autorisation ne couvre que les appels passant par lui.
- **Profils** : signature HMAC facultative selon les paramètres de chargement, avec secret partagé; ce n’est pas une infrastructure de signature à clés publiques ni une gestion de confiance/rotation démontrée.

## Secrets et données

Les variables d’environnement prévues sont `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_APP_SECRET`, `WHATSAPP_VERIFY_TOKEN`, `HOLD_STUDIO_TOKEN`; le secret de profil est remis à `load_profile(..., secret=...)`. Le README illustre l’export de secrets dans le shell, sans prescription de coffre. Les secrets ne doivent pas être intégrés aux profils/journaux, images de conteneur, dépôts, arguments de ligne de commande ou traces d’erreur. Il faut privilégier un gestionnaire de secrets, permissions minimales, rotation et procédures de réponse à divulgation.

Le journal et `relay_messages` peuvent contenir données d’action, reçus, identifiants d’opération, questions, expéditeur masqué et erreurs. Le masquage du numéro dans certaines métadonnées n’est pas une anonymisation globale. Les données personnelles et métiers doivent être limitées, protégées et soumises à une durée de conservation définie. Pas de chiffrement au repos montré dans `Store`.

## Réseau et exploitation

`Habilitation.serve()` et `serve()` lient par défaut le serveur à `127.0.0.1`, port 8787; `serve(host=...)` peut toutefois l’exposer ailleurs. Le webhook doit être publiquement joignable pour WhatsApp et le README recommande un tunnel HTTPS; cette exposition ne doit pas entraîner celle des routes de mission. Les contrôles d’en-têtes forwarding ne remplacent pas une configuration de proxy fiable. L’API sortante utilise Graph API HTTPS par défaut, mais l’URL de base et version sont configurables : verrouiller l’endpoint et contrôler les sorties réseau.

En production, séparer l’exposition webhook de l’interface d’administration si possible, imposer TLS au point d’entrée, n’autoriser les ports nécessaires, protéger Studio par authentification forte derrière un proxy de confiance, limiter taille/débit, surveiller erreurs et saturation et ne pas publier les journaux. Vérifier que le processus de l’agent, le serveur et les workers partagent le magasin attendu et que seul HOLD accède aux credentials d’effet.

## Sauvegardes et intégrité probante

La transaction SQLite et WAL protègent la cohérence locale dans certains scénarios de panne; ce n’est pas une stratégie de sauvegarde. Les tests de concurrence et de rejeu montrent des propriétés logicielles dans des scénarios contrôlés, mais ne démontrent pas la résilience opérationnelle. Avant usage réel, établir sauvegardes cohérentes (incluant gestion WAL), chiffrement et accès restreint, copie indépendante/hors hôte, objectifs RPO/RTO, tests de restauration et réconciliation avec l’état du fournisseur après restauration.

**Interprétation de la chaîne :** la chaîne de hachage peut détecter une altération qui ne met pas à jour les liens ultérieurs. Elle ne prouve pas que tous les événements ont été enregistrés, qu’aucun événement n’a été supprimé/tronqué, ni que les événements sont vrais. Un acteur capable de réécrire la base entière peut recalculer la chaîne; les relectures d’état/décision apportent des contrôles de cohérence supplémentaires, pas une preuve indépendante de l’historique réel. Un témoin externe périodique et indépendant est nécessaire si l’on exige une preuve d’altération/réécriture contre l’administrateur du stockage.

## Priorités de réduction du risque

1. **Avant tout effet réel :** confiner les identifiants fournisseurs dans les seules fonctions d’outil HOLD; documenter chaque action, profil et garantie fournisseur d’idempotence; tester les scénarios de timeout, redémarrage, double processus et réconciliation réelle.
2. **Sécuriser identité et administration :** imposer la signature des profils selon une politique documentée, protéger/rotater les clés; renforcer l’authentification de l’autorité et du Studio, limiter les privilèges, éviter les secrets partagés permanents et définir révocation d’urgence.
3. **Protéger persistance et preuves :** stockage chiffré et contrôlé, sauvegardes vérifiées/restaurations testées, journal externe append-only/signé si la preuve est une exigence; surveiller toute divergence avec le fournisseur.
4. **Durcir le service :** TLS, proxy de confiance explicite, filtrage réseau, limites anti-abus, séparation webhook/Studio, restrictions de sortie, supervision/alertes et procédure d’incident.
5. **Traiter confidentialité et multi-tenant :** définir classification, minimisation, rétention/suppression, contrôle d’accès par mission/tenant et vérifications avant toute exploitation partagée.

## Références dans le dépôt

- `hold/runtime.py` : `Habilitation`, `binding()`, `_register()`, `open_mission()`, `mission()`, `Mission.dispatch()`, `answer()`, `revoke()`, `effect_guarantees()`.
- `hold/floor.py` : `INVARIANTS`, `admit_action()`, `provenance_match()`, règles d’enveloppe et d’approbation.
- `hold/profile.py` : modèles `Profile`, `Authority`, `Power`; `sign()`, `signature_valid()`, `load_profile()`.
- `hold/store.py` : `SCHEMA`, `Store.__init__()`, `tx()`, `append()` et contraintes uniques.
- `hold/journal.py` : format d’événement, construction et vérification de chaîne.
- `hold/relay/whatsapp.py` : `WhatsAppConfig`, `WhatsAppRelay`, `verify_signature()`, `parse_webhook()`, `Inbound`.
- `hold/server.py` : `create_app()`, `studio_guard()`, endpoints webhook/Studio, `serve()`.
- Tests : `tests/test_profile_floor.py`, `test_authority_binding.py`, `test_runtime.py`, `test_relay_server.py`, `test_studio_parity.py`, `test_showcase.py`, `test_guarantees.py`.
- `README.md` : garanties explicitement limitées, hypothèse de credentials réservés à HOLD, limites du prototype et chaîne de hachage.
