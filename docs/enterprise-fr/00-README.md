# HOLD — documentation technique et opérationnelle

> **Mise à jour d'intégration (3 novembre 2026).** Experience v1 est intégrée à `main` dans le même commit que ce dossier, avec deux ajouts : `python -m experience.verify` revérifie désormais le journal lui-même (chaîne, positions, verdicts, statut, état impliqué contre le store exporté), et plus seulement les empreintes du manifeste ; `python -m hold.journal_verify` exécute les contrôles de `hold journal verify` avec un code de retour correct, en attendant la correction du défaut `dr` dans `hold/cli.py` (fichier gelé). Au moment de l'intégration : `pytest` 174 passed (sandbox Linux), `hold bench check` conforme au protocole gelé. Les mentions « ZIP non publié ni fusionné » plus bas décrivent l'état antérieur ; le registre 06 se termine par un addendum.

**Dossier technique v1.0 :** décrit `main` au commit `d4ab8acf11f5cea409104753d195370a0769c156`, et ses 160 tests observés à cette date. **Mise à jour de version et claims :** [06 — Registre des affirmations et des preuves](06-claims-evidence-register.md), qui recense le HEAD public `b94a1e90ba6ed0223b6a24c667a3eb2f5ae1182c`, l'expérience locale annoncée et les résultats E1-A6. **Tag gelé :** `e1-a6-frozen`, résolu au commit `12250bca32b9fbc77113994db8a68036f9948f9d`. Ne pas confondre dossier technique historique, code public actuel, branche de démonstration locale et protocole gelé.

HOLD est un prototype de recherche qui place un contrôle d’admissibilité logiciel devant les outils d’un agent. La révision historique examinée comporte un runtime Python, un plancher de règles, des profils YAML, une persistance SQLite, un journal à chaînage de hachages, un rejeu et des intégrations optionnelles de serveur et de relais. Les **160 tests** se rapportent seulement au commit historique indiqué. Le HEAD public ultérieur a passé **167 tests** dans le sandbox Linux selon le registre 06. Aucun de ces résultats ne constitue un audit indépendant, une certification ou une preuve de production.

## Lire la documentation

- [01 — Architecture technique](01-architecture.md) décrit les composants, le cycle d’action, les profils, les états, les intégrations et les limites des preuves.
- [02 — Modèle de menace](02-security-threat-model.md) examine actifs, frontières de confiance, menaces, contrôles présents, contrôles absents et réductions de risque.
- [03 — Runbook opérationnel](03-operations-runbook.md) donne l’installation Linux/WSL, les contrôles, les commandes réellement disponibles, le lancement du serveur, les incidents, la sauvegarde et le retour arrière.
- [04 — Intégration et validation](04-integration-and-validation.md) décrit le câblage des outils, le profil, les résultats typés, l’exemple synthétique et la stratégie de validation.
- [05 — Décision de préparation à l’entreprise](05-enterprise-readiness.md) liste les prérequis bloquants avant tout pilote avec des effets réels.
- [06 — Registre des affirmations et des preuves](06-claims-evidence-register.md) est la référence de version à utiliser pour les claims, leurs statuts et les réserves, y compris le prototype Experience v1 annoncé sur branche locale.

## Statut opérationnel : prototype, pas prêt pour une production d’entreprise

Ne pas connecter HOLD à des effets métier réels sur la seule base de ce dépôt. Les campagnes et l’exemple fournisseur emploient un fournisseur synthétique. L’intégration réelle, la disponibilité et l’idempotence d’un fournisseur, l’authentification forte des utilisateurs et la résilience d’exploitation ne sont pas qualifiées ici. L’expérimentation E1-A6 est exploratoire, porte sur un modèle et un environnement simulé, et a été conçue en partie après examen des traces E1-A5 sur les mêmes scénarios.

Le défaut CLI `dr` non défini a été reproduit sur le HEAD public `b94a1e9` : `hold journal verify <db> <mission>` affiche ses résultats, puis échoue par `NameError`. Ne pas considérer cette commande comme un contrôle vert avant correction et test de régression. L’archive Experience v1 a été reçue et ses cinq tests passent dans un clone scratch de `b94a1e9`; ce ZIP n’est pas publié ni fusionné à `main`. Voir le registre 06 pour ses preuves, son empreinte et ses réserves.

Le contrôle expérimental du gel (`hold bench check`) confirme la correspondance entre le protocole figé et le code du checkout correspondant. Sur le checkout public observé précédemment, les revalidations des traces sont cohérentes pour A et C, mais B présente trois divergences descriptives S11 documentées; les champs de verdict H1/H2 sont rapportés identiques selon l’erratum PMO. Le rejeu des fichiers bruts ne relance pas le modèle et ne vérifie pas les effets d’un fournisseur réel.

## Échelle de preuve utilisée

Les documents distinguent :

1. **Implémenté dans le code** — fonction ou contrôle identifié dans la révision de référence.
2. **Couvert par des tests** — scénarios logiciels déterminés, souvent synthétiques ou mockés.
3. **Observation expérimentale** — résultat d’une campagne particulière, avec modèle, scénarios, répétitions et réserves.
4. **Non établi** — propriété qui exigerait des essais réels, une vérification indépendante ou un mécanisme non présent.

« Chaîne valide » signifie que les événements présents satisfont le calcul de chaîne et les validations du format. Cela ne prouve pas la complétude, la vérité externe ou l’impossibilité de réécrire l’ensemble de la base et recalculer les empreintes.

## Hypothèses d’utilisation

Les informations opérationnelles et les extraits de configuration doivent être adaptés et validés dans l’environnement cible. Les profils d’exemple ne sont pas des mandats de production. Les adaptateurs doivent garder les identifiants fournisseur derrière HOLD. Les garanties de réservation anti-doublon dépendent d’un même stockage partagé et ne se propagent pas à plusieurs bases ou hôtes. Toute configuration réelle doit faire l’objet d’une revue sécurité, confidentialité, fournisseur et exploitation.

Cette documentation est une analyse du commit cité; elle n’applique aucun changement au dépôt public et ne crée aucun engagement de support, SLA ou compatibilité de plateforme. Pour une décision ou un partage externe, joindre le registre 06 afin que les limites de version soient visibles.
