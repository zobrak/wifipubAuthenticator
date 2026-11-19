# Journal des modifications

Toutes les modifications notables du projet sont consignées ici.
Format : [Keep a Changelog 1.1.0](https://keepachangelog.com/fr/1.1.0/) ;
numérotation : [Semantic Versioning 2.0.0](https://semver.org/lang/fr/).
Le numéro de la version courante est dans le fichier `VERSION`.

## [0.10.0] - 2026-11-19

### Ajouté
- portail/opnsense/index.html : page de connexion à champ unique (numéro, puis code reçu), mise en page neutre.
- Limites par fournisseur : OpnsenseLimit* et OvhSmsLimit*, en plus des limites générales.
- install.conf : ServiceName (nom du service).

### Modifié
- Template SMS livré : code de forme {mot_de_passe}-{identifiant}.
- install.sh : section ÉDITION supprimée, remplacée par ServiceName.
- README.md : configuration d'OPNsense détaillée (serveur Voucher, page de connexion, zone).

## [0.9.2] - 2026-11-19

### Ajouté
- README.md : présentation, prérequis, installation, configuration, commandes, journal.

## [0.9.1] - 2026-11-19

### Ajouté
- install.sh : renomme les mots-clés obsolètes de conf.d (valeurs inchangées, sauvegarde), sauf refus.

## [0.9.0] - 2026-11-19

### Ajouté
- Templates des messages : /etc/wifipubauth/templates/FOURNISSEUR-CANAL.conf (opnsense-sms.conf), personnalisation dans templates.d/.
- Language : langue du système par défaut ; repli sur en-US si le template ne la contient pas.

### Modifié
- Textes des SMS déplacés de i18n/ vers les templates ; CatalogDirectory obsolète (texte personnalisé encore lu, avec avertissement).

### Supprimé
- /usr/local/lib/wifipubauth/i18n.

## [0.8.0] - 2026-11-19

### Ajouté
- install.sh : copie des textes livrés dans /etc/wifipubauth/i18n, une seule fois, pour personnalisation.

### Modifié
- CatalogDirectory : /etc/wifipubauth/i18n par défaut ; ses textes surchargent les textes livrés, clé par clé.

## [0.7.3] - 2026-11-19

### Ajouté
- wifipubauthctl help [COMMANDE].

### Modifié
- wifipubauthctl status : état résumé ; état complet seulement pour un service en défaut.
- wifipubauthctl check : sortie en lecture seule (sans [  OK  ]).
- install.sh : fin d'installation avec wifipubauthctl check, status et help.

## [0.7.2] - 2026-11-19

### Ajouté
- install.sh : affiche en fin d'installation la sortie de wifipubauthctl check, status et -h.

## [0.7.1] - 2026-11-19

### Corrigé
- Site FreeRADIUS reconnu quel que soit le nom de son fichier (wifipubauthctl check, install.sh).
- Mise à jour : VoucherApiProvider et SmsApiProvider déduits des paramètres du fournisseur déjà configurés.

## [0.7.0] - 2026-11-19

### Ajouté
- install.conf : paramètres d'installation ; install.sh refuse de démarrer si un paramètre obligatoire manque.
- install.sh : saisie interactive des secrets manquants.
- install.sh : création du site FreeRADIUS (secret RADIUS généré), activation, désactivation des exemples.
- VoucherApiProvider : choix du fournisseur de vouchers (opnsense).

### Modifié
- VoucherProvider renommé OpnsenseVoucherServer (ancien nom accepté, avec avertissement).
- Événement opnsense_failed renommé voucher_failed (MESSAGE_ID inchangé).
- wifipubauthctl set : entrée en attente vidée avant chaque saisie de secret, trois essais.
- Commentaires neutres, sans référence à un portail captif particulier.

## [0.6.1] - 2026-11-19

### Modifié
- Configuration par défaut : commentaires réduits.

## [0.6.0] - 2026-11-19

### Ajouté
- wifipubauthctl set : écriture contrôlée de la configuration personnalisée ;
  secrets en saisie interactive seulement.
- SmsApiProvider : choix du fournisseur SMS (ovh).
- Pseudonymization on|off (off par défaut) ; set Pseudonymization on --genkey.

### Modifié
- Paramètres OVHcloud renommés : OvhSmsUrl, OvhSmsAccount, OvhSmsLogin,
  OvhSmsPassword, OvhSmsSender (anciens noms acceptés, avec avertissement).
- wifipubauthctl réécrit en Python.

### Supprimé
- wifipubauthctl logs (journalctl -u wifipubauth).

## [0.5.0] - 2026-11-19

### Ajouté
- wifipubauthctl : contrôle du service (status, start, stop, restart, enable,
  disable, check, logs, genkey, version).

## [0.4.5] - 2026-11-19

### Ajouté
- Option --generer-cle : génère une clé pour FingerprintKey.

## [0.4.4] - 2026-11-19

### Modifié
- Journal : identifiant du visiteur en clair par défaut (WPA_IDENTIFIER).
- FingerprintKey facultative : si définie, empreinte à la place (WPA_HID).

## [0.4.3] - 2026-11-19

### Supprimé
- install.sh : prise en charge de l'ancien nom du service.

### Modifié
- Texte du SMS par défaut neutre (fr-FR, en-US).

## [0.4.2] - 2026-11-19

### Modifié
- install.sh : messages sur l'ancienne configuration clarifiés.

## [0.4.1] - 2026-11-18

### Modifié
- install.sh : sorties réduites.

### Corrigé
- install.sh : code de mise en gras affiché en clair.

## [0.4.0] - 2026-11-18

### Modifié
- `install.sh` réécrit : vérifications, puis plan (notes de version, paquets,
  compte, dossiers, fichiers ajoutés, mis à jour ou modifiés localement,
  configuration rendue obsolète) à valider avant toute modification ;
  installation de la configuration par défaut seulement ; démarrage des
  services dont la configuration est complète et valide ; marche à suivre
  pour la configuration personnalisée affichée à la fin.
  - Seuls les ajouts et modifications sont affichés ; `[  OK  ]` en vert,
    `[FAILED]` en rouge, `[ WARN ]` en jaune (désactivé hors terminal ou
    avec `NO_COLOR`).
  - Journal détaillé `install.log` à côté du script (sortie d'apt comprise,
    aucun secret).
  - Fichiers modifiés localement repérés grâce aux empreintes enregistrées
    (`MANIFEST`) et à la version installée (`VERSION`), dans
    `/usr/local/lib/wifipubauth/` ; sauvegardés avant remplacement.
  - `--simulation` : vérifications et plan seulement.
  - Installation précédente sous un autre nom (`wifipublicauthenticator`)
    signalée : service arrêté, configuration à reporter, fichiers à
    supprimer listés.
- `wifipubauth.conf.example` : devient la configuration par défaut, avec la
  marche à suivre pour la configuration personnalisée (`conf.d/`) et un
  exemple ; `VoucherProvider` vaut par défaut le nom du service.
- FreeRADIUS : le serveur virtuel est livré comme modèle
  (`sites-available/wifipubauth.example`), avec sa mise en service décrite
  en tête ; le module rest est installé et activé par `install.sh`.
- Service : lecture de configuration séparée (`lire_valeurs`) pour permettre
  à `install.sh` de repérer les réglages manquants.

### Supprimé
- Saisies interactives de la 0.3.0 (réglages du service, adresses et secret
  RADIUS) : la configuration personnalisée se fait à la main, guidée.

## [0.3.0] - 2026-11-18

### Ajouté
- `install.sh`, étape 9 : configuration de FreeRADIUS.
  - Serveur virtuel dédié (`freeradius/site.conf`) : écoute en
    authentification seule sur l'adresse de la machine vue par OPNsense ;
    un seul client, OPNsense, avec Message-Authenticator exigé (BlastRADIUS) ;
    premier filtre (identifiant au format mobile 06/07 ou e-mail, mot de
    passe présent), puis module rest, puis refus systématique.
  - Module rest (`freeradius/module.conf`) vers l'adresse et le port effectifs
    du service, lus dans sa configuration.
  - Deux saisies avec proposition (adresse d'OPNsense, tirée de l'URL de son
    API ; adresse d'écoute, tirée de la route vers OPNsense) ; secret RADIUS
    de 128 bits généré, écrit en `freerad:freerad 0640`, affiché une fois.
  - Retrait, après confirmation, des liens d'activation des serveurs
    `default` et `inner-tunnel` et du module `eap` (fichiers Debian conservés).
  - Vérification par `freeradius -XC` avant tout démarrage.
- Service : le processus porte le nom du service (`/proc/self/comm`, 15
  caractères au plus, limite du noyau) au lieu de `python3`.

### Modifié
- Nom du projet et du service : `wifipubauth` (anciennement
  `wifipublicauthenticator`) ; fichiers renommés (`wifipubauth.py`,
  `wifipubauth.conf.example`, `systemd/wifipubauth.service`). Le serveur
  Voucher d'OPNsense attendu s'appelle désormais `wifipubauth`.
- Service : nom du journal (`SYSLOG_IDENTIFIER`) et nom du processus tirés du
  nom du programme installé, donc de `NOM_SERVICE`.
- `install.sh`, étape 4 : FreeRADIUS déjà configuré pour le service n'est
  plus mis au repos lors d'une relance.

## [0.2.2] - 2026-11-18

### Modifié
- Journal : protocole natif de journald au lieu de lignes JSON. Chaque
  événement a un message lisible (`MESSAGE`), un niveau syslog (`PRIORITY`),
  un identifiant stable par type d'événement (`MESSAGE_ID`),
  `SYSLOG_IDENTIFIER`, et des champs indexés préfixés `WPA_` (`WPA_EVENT`,
  `WPA_HID`, `WPA_VOUCHER`, `WPA_DETAIL`...). Détection du journal par
  `$JOURNAL_STREAM` ; hors systemd, ou si l'envoi échoue, ligne de texte sur
  la sortie d'erreur avec préfixe de niveau `<N>`. Codes d'événement inchangés.

## [0.2.1] - 2026-11-18

### Modifié
- `install.sh` : le nom du serveur Voucher d'OPNsense n'est plus demandé,
  c'est le nom du service ; 7 saisies au lieu de 8.
- `wifipublicauthenticator.conf.example` : commentaire de `VoucherProvider`
  mis en accord.

## [0.2.0] - 2026-11-18

### Ajouté
- `wifipublicauthenticator.py` : le service. Écoute FreeRADIUS (module rest,
  corps JSON) sur 127.0.0.1, répond toujours 401 ; pour une demande valide
  (mobile français 06/07 + marqueur), crée un voucher via l'API d'OPNsense et
  l'envoie par SMS (http2sms OVHcloud), en tâche de fond. Limites par numéro,
  par MAC et globale ; journal JSON avec empreinte HMAC des numéros.
- `wifipublicauthenticator.conf.example` : configuration complète commentée,
  au format OpenSSH, Include de `conf.d/*.conf` en tête.
- `i18n/fr-FR.json`, `i18n/en-US.json` : texte du SMS.
- `systemd/wifipublicauthenticator.service` : unité durcie
  (`systemd-analyze security` : 1.2).
- `tests/` : tests pytest sans réseau (faux OPNsense et faux OVH).
- `install.sh` : étapes 6 à 8 (application, configuration avec 8 saisies dont
  2 masquées et clé d'empreinte générée, activation du service) ; un fichier
  existant différent n'est remplacé qu'après confirmation et sauvegarde.

## [0.1.5] - 2026-11-18

### Modifié
- `install.sh` : l'historique quitte l'en-tête du script pour ce fichier ;
  le numéro affiché est celui du projet, lu dans `VERSION`.

## [0.1.4] - 2026-11-18

### Modifié
- `install.sh` : plus de numéro de version en dur, il est lu dans le
  fichier `VERSION` placé à côté du script.

## [0.1.3] - 2026-11-18

### Modifié
- `install.sh` : retour du contrôle du nom à la règle de la 0.1.1
  (majuscules admises) ; l'obligation des minuscules n'était pas demandée.

## [0.1.2] - 2026-11-18

### Modifié
- `install.sh` : nom du service en minuscules, `wifipublicauthenticator`.

## [0.1.1] - 2026-11-18

### Modifié
- Script renommé `install.sh`.
- Un seul réglage à éditer, le nom du service, dont dérivent le compte et
  les dossiers ; nom contrôlé au démarrage ; surcharges dans `conf.d`.

## [0.1.0] - 2026-11-18

### Ajouté
- Script d'installation : prérequis, dépendances (FreeRADIUS, module `rest`,
  Python 3), mise au repos de FreeRADIUS, compte de service, dossiers.
