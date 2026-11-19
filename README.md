# wifipubauth

Inscription à un Wi-Fi public par SMS, derrière un portail captif.

Le visiteur saisit son numéro de mobile sur la page du portail captif. Il reçoit
par SMS un code d'accès temporaire (voucher), qu'il colle dans le même champ
pour se connecter.
Aucun compte n'est créé à l'avance, aucune donnée n'est demandée au-delà du
numéro.

Licence : AGPL-3.0-or-later.
Aide et signalement de bugs : https://github.com/zobrak/wifibpub

---

## Sommaire

1. [Fonctionnement](#fonctionnement)
2. [Écosystème et prérequis](#écosystème-et-prérequis)
3. [Ce que fait l'application aujourd'hui](#ce-que-fait-lapplication-aujourdhui)
4. [Évolutions prévues](#évolutions-prévues)
5. [Installation](#installation)
6. [Mise à jour](#mise-à-jour)
7. [Configuration du portail captif (OPNsense)](#configuration-du-portail-captif-opnsense)
8. [Configuration de wifipubauth](#configuration-de-wifipubauth)
9. [Templates des messages](#templates-des-messages)
10. [Commande wifipubauthctl](#commande-wifipubauthctl)
11. [Journal](#journal)
12. [Sécurité](#sécurité)
13. [Fichiers installés](#fichiers-installés)
14. [Désinstallation](#désinstallation)
15. [Développement et tests](#développement-et-tests)

---

## Fonctionnement

```
 Visiteur               Portail captif           FreeRADIUS            wifipubauth          Fournisseurs
 (navigateur)           (OPNsense)               (serveur virtuel)     (service local)
     │                       │                        │                      │                    │
 1   │ numéro + marqueur ───►│                        │                      │                    │
 2   │                       │ serveur Voucher : refus│                      │                    │
 3   │                       │ Access-Request ───────►│                      │                    │
 4   │                       │                        │ filtre, module rest ►│                    │
 5   │                       │◄────────── Access-Reject (toujours) ◄─────────│                    │
 6   │                       │                        │                      │ voucher (API) ────►│ OPNsense
 7   │                       │                        │                      │ SMS ──────────────►│ OVHcloud
 8   │◄──────────────────────────────────── SMS : code MOTDEPASSE-IDENTIFIANT ──────────────────│
 9   │ code ────────────────►│ serveur Voucher : accès accordé                                    │
```

1. Sur la page du portail, le visiteur saisit son numéro de mobile. La page
   l'envoie comme identifiant, avec le marqueur de demande (`RequestMarker`,
   `demande` par défaut) comme mot de passe.
2. Le portail essaie d'abord le serveur Voucher, qui refuse : ce n'est pas un
   voucher.
3. Le portail interroge alors le serveur RADIUS.
4. Le serveur virtuel FreeRADIUS fourni ne connaît que le portail captif. Il
   écarte les requêtes mal formées et transmet les autres au service local
   (module `rest`).
5. Le service répond toujours par un refus : il n'ouvre jamais l'accès
   lui-même.
6. Si la demande est valide et dans les limites, il demande en tâche de fond un
   voucher à l'API du portail captif.
7. Il envoie le voucher par SMS, sous la forme d'un code
   `MOTDEPASSE-IDENTIFIANT`.
8. Le visiteur reçoit le SMS.
9. Il colle le code dans le champ de la page, qui le sépare en identifiant et
   mot de passe : c'est le portail captif qui accorde l'accès, pour la durée de
   validité du voucher.

---

## Écosystème et prérequis

### Machine d'installation

| Élément | Exigence |
|---|---|
| Système | Debian 13 ou dérivée, avec systemd actif |
| Python | 3.11 ou plus (bibliothèque standard seulement) |
| FreeRADIUS | 3.2.7 ou plus, avec le module `rest` |
| Droits | root pour l'installation |
| Réseau | joignable en RADIUS (UDP 1812) depuis le portail captif ; accès HTTPS à l'API du portail et au fournisseur SMS |

Les paquets manquants (`freeradius`, `freeradius-rest`, `freeradius-utils`,
`python3`, `ca-certificates`) sont installés par `install.sh`.

### Portail captif

Fournisseur pris en charge : **OPNsense**, avec :

- le portail captif (Services › Captive Portal) ;
- un serveur d'authentification de type Voucher ;
- un utilisateur d'API muni du seul privilège « Services: Captive Portal ».

### Fournisseur SMS

Fournisseur pris en charge : **OVHcloud** (API http2sms), avec un compte SMS, un
utilisateur SMS et un expéditeur validé.

### Intégration

```
                 ┌───────────────── machine wifipubauth ─────────────────┐
 Portail captif  │  FreeRADIUS          wifipubauth           journald   │
 (OPNsense) ─────┼─► :1812 ──rest──► 127.0.0.1:8642 ──────────► (journal) │
      ▲          │                         │                              │
      │          └─────────────────────────┼──────────────────────────────┘
      └──────── API vouchers (HTTPS) ◄─────┤
                                           └──► API SMS (HTTPS) : OVHcloud
```

---

## Ce que fait l'application aujourd'hui

- Canal : **SMS**.
- Identifiant accepté : numéro de mobile français (06 ou 07, avec ou sans
  espaces ou points). Une adresse e-mail est reconnue mais refusée tant que le
  canal e-mail n'existe pas.
- Fournisseur de vouchers : **OPNsense** (`VoucherApiProvider opnsense`).
- Fournisseur SMS : **OVHcloud** (`SmsApiProvider ovh`).
- Limites de demandes par numéro, par appareil (adresse MAC transmise par le
  portail) et pour l'ensemble du service ; limites supplémentaires par
  fournisseur.
- Textes des SMS personnalisables par langue (templates).
- Page de connexion pour le portail captif d'OPNsense, à champ unique (numéro,
  puis code reçu).
- Journal structuré dans journald.
- Installation, mise à jour et contrôle « à la Debian » : `install.sh`,
  `wifipubauthctl`, configuration au format OpenSSH avec surcharges `conf.d/`.

## Évolutions prévues

- Canal **e-mail** (template `opnsense-mail.conf`).
- Autres fournisseurs de vouchers (autres portails captifs) et autres
  fournisseurs SMS, sur le modèle de `VoucherApiProvider` et `SmsApiProvider`.
- Intégration comme greffon de Paresia.

---

## Installation

### 1. Préparer le portail captif

Avant l'installation, créer côté OPNsense le serveur Voucher et la clé d'API :
voir [Configuration du portail captif](#configuration-du-portail-captif-opnsense),
étapes 1 et 2. Les étapes suivantes se font après l'installation.

### 2. Extraire l'archive

```
tar xzf wifipubauth-VERSION.tar.gz
cd wifipub
```

### 3. Compléter `install.conf`

`install.conf` réunit, une fois pour toutes, les paramètres nécessaires à
l'installation.

- Lignes sans `#` : obligatoires, à compléter (`MotClé valeur`).
- Lignes en `#` : valeurs par défaut ; pour en changer, retirer le `#` et
  modifier la valeur.
- Les secrets n'y figurent pas : `install.sh` les demande.

Paramètres obligatoires à la première installation :

| Paramètre | Rôle |
|---|---|
| `RadiusListenAddress` | adresse IP de cette machine vue du portail captif (écoute de FreeRADIUS) |
| `RadiusClientAddress` | adresse IP d'où le portail captif envoie ses requêtes RADIUS |
| `VoucherApiProvider` | fournisseur des vouchers : `opnsense` |
| `OpnsenseUrl` | adresse de l'API d'OPNsense, par exemple `https://opnsense.example.org` |
| `OpnsenseApiKey` | clé de l'API |
| `SmsApiProvider` | fournisseur SMS : `ovh` |
| `OvhSmsAccount` | compte SMS, par exemple `sms-xx00000-1` |
| `OvhSmsLogin` | utilisateur SMS |
| `OvhSmsSender` | expéditeur |

`install.sh` refuse de démarrer tant qu'un paramètre obligatoire manque, et en
donne la liste.

Paramètre facultatif propre à l'installation :

| Paramètre | Défaut | Rôle |
|---|---|---|
| `ServiceName` | `wifipubauth` | nom du service : compte système, dossiers, commandes (lettres, chiffres, `_` et `-`) ; à fixer avant la première installation |

Tous les autres paramètres de la [configuration](#configuration-de-wifipubauth)
peuvent aussi y être donnés.

### 4. Lancer l'installation

```
sudo ./install.sh --simulation   # facultatif : vérifications et plan, sans modification
sudo ./install.sh
```

Options :

| Option | Effet |
|---|---|
| `--simulation` | vérifications et plan seulement, rien n'est modifié |
| `--oui` | répond oui aux confirmations (les secrets restent demandés) |
| `--aide` | aide |

Déroulement :

1. **Vérifications** : système, outils, fichiers du projet, `install.conf`.
   Rien n'est modifié.
2. **Plan**, à valider : paquets, fichiers à installer ou mettre à jour,
   paramètres à enregistrer, secrets à saisir, autres opérations.
3. **Installation** :
   - paquets, compte système `wifipubauth` (sans shell ni dossier personnel),
     dossiers, fichiers ;
   - paramètres d'`install.conf` écrits dans `conf.d/` ;
   - saisie masquée des secrets (`OpnsenseApiSecret`, `OvhSmsPassword`), avec
     confirmation et trois essais ;
   - création du serveur virtuel FreeRADIUS avec un secret partagé généré,
     activation, désactivation des liens d'exemple de Debian (`default`,
     `inner-tunnel`, `eap`).
4. **Services** : démarrage de `wifipubauth` et de FreeRADIUS, chacun seulement
   si sa configuration est valide.
5. **Bilan** : sorties de `wifipubauthctl check`, `status` et `help`.

À la fin, `install.conf` est renommé `install.conf.old`. Le journal détaillé de
l'installation, sans aucun secret, est écrit dans `install.log`, à côté du
script.

Le secret RADIUS est à reporter dans le portail captif. Il se lit dans le
fichier du site FreeRADIUS, indiqué en fin d'installation :

```
sudo grep 'secret =' /etc/freeradius/3.0/sites-available/wifipubauth
```

---

## Mise à jour

```
tar xzf wifipubauth-VERSION.tar.gz
cd wifipub
sudo ./install.sh
```

- Les notes de version depuis la version installée sont affichées avec le plan.
- La configuration personnelle (`conf.d/`, `templates.d/`, site FreeRADIUS) est
  conservée.
- `install.conf` n'est exigé que si la nouvelle version introduit un paramètre
  obligatoire absent de la configuration. Ses valeurs sont alors ajoutées à
  `install.conf.old`.
- Les mots-clés obsolètes de `conf.d/` sont renommés, valeurs inchangées, avec
  copie de sauvegarde, sauf refus à la question posée.
- Un fichier livré modifié localement est sauvegardé (`.sauvegarde-DATE`) avant
  d'être remplacé.

---

## Configuration du portail captif (OPNsense)

Aucune option avancée n'est nécessaire : le portail transmet de lui-même
l'adresse MAC du client au serveur RADIUS (`Calling-Station-Id`).

### 1. Serveur Voucher

System › Access › Servers › Add :

| Champ | Valeur |
|---|---|
| Descriptive name | `wifipubauth` (valeur par défaut d'`OpnsenseVoucherServer`) |
| Type | Voucher |
| Use simple passwords (less secure) | coché |
| Username length | `2` |
| Password length | `10` |

Avec ces valeurs, le code envoyé par SMS fait 13 caractères
(`MOTDEPASSE-ID`). « Use simple passwords » est indispensable : le jeu de
caractères simple ne contient pas de tiret, ce qui permet à la page de séparer
le code sans ambiguïté.

### 2. Utilisateur et clé d'API

System › Access › Users › Add :

1. créer un utilisateur dédié, par exemple `wifipubauth` ;
2. ne lui donner que le privilège « Services: Captive Portal » ;
3. enregistrer, puis, dans la section des clés d'API, créer une clé : le
   fichier téléchargé contient `key=` (`OpnsenseApiKey`) et `secret=`
   (`OpnsenseApiSecret`, demandé par `install.sh`).

Recommandé : au pare-feu ou au proxy inverse, n'autoriser depuis la machine
wifipubauth que l'adresse
`/api/captiveportal/voucher/generateVouchers/<serveur Voucher>`.

### 3. Serveur RADIUS

Après l'installation. System › Access › Servers › Add :

| Champ | Valeur |
|---|---|
| Descriptive name | au choix, par exemple `wifipubauth-radius` |
| Type | Radius |
| Hostname or IP address | `RadiusListenAddress` |
| Shared Secret | secret du site FreeRADIUS (voir fin d'installation) |
| Services offered | Authentication |
| Authentication port | `1812` |
| Protocol | PAP |

Vérification sans déclencher d'envoi : System › Access › Tester, serveur
RADIUS ci-dessus, identifiant `test`, mot de passe `test`. Un refus est
attendu ; aucun SMS n'est envoyé.

### 4. Page de connexion

La page livrée, `portail/opnsense/index.html`, est documentée en tête de
fichier. Mise en page neutre, à adapter. Pour l'installer :

```
cd portail/opnsense
zip opnsense-template.zip index.html
```

Services › Captive Portal › Administration, onglet Templates › Add : nom au
choix, fichier `opnsense-template.zip`. Les fichiers absents du zip (CSS,
scripts, images) sont repris du template par défaut d'OPNsense.

La page attend un SMS de forme `{mot_de_passe}-{identifiant}` (template livré)
et un `MARQUEUR` égal à `RequestMarker`.

### 5. Zone du portail captif

Services › Captive Portal › Administration, onglet Zones, zone du Wi-Fi public :

| Champ | Valeur |
|---|---|
| Authenticate using | le serveur Voucher `wifipubauth`, **puis** le serveur RADIUS |
| Custom template | le template de l'étape 4 |

Le portail essaie les serveurs dans l'ordre et s'arrête au premier qui
accepte : un code est accepté par le serveur Voucher ; une demande (numéro et
marqueur) est refusée par le serveur Voucher puis transmise à FreeRADIUS.

Enregistrer, puis Apply.

---

## Configuration de wifipubauth

### Fichiers

| Fichier | Rôle |
|---|---|
| `/etc/wifipubauth/wifipubauth.conf` | configuration par défaut, remplacée à chaque mise à jour |
| `/etc/wifipubauth/conf.d/*.conf` | configuration personnelle, prioritaire, jamais remplacée |

Format OpenSSH : `MotClé valeur`, un mot-clé par ligne, `#` pour les
commentaires, `Include` pour les fichiers inclus. La première valeur lue
l'emporte : `conf.d/` est inclus en tête du fichier par défaut.

La méthode recommandée pour modifier la configuration est
`wifipubauthctl set` : la valeur est contrôlée avant d'être écrite.

```
sudo wifipubauthctl set VoucherValidity 24h
sudo wifipubauthctl set OvhSmsPassword          # secret : saisie interactive
sudo wifipubauthctl check
sudo wifipubauthctl restart
```

Durées : nombre suivi de `s`, `m`, `h` ou `d`. Limites : `nombre/durée`.

### Paramètres

**Service**

| Paramètre | Défaut | Rôle |
|---|---|---|
| `ListenAddress` | `127.0.0.1` | adresse d'écoute (requêtes de FreeRADIUS) |
| `ListenPort` | `8642` | port d'écoute |
| `Channels` | `sms` | méthodes d'authentification ; disponible : `sms` |
| `RequestMarker` | `demande` | mot de passe qui signale une demande de code |
| `Language` | langue du système | langue des messages, forme `fr-FR` |
| `Pseudonymization` | `off` | `on` : numéros remplacés dans le journal par une empreinte |
| `FingerprintKey` | — | clé de l'empreinte ; secret ; `wifipubauthctl set Pseudonymization on --genkey` |

**Vouchers**

| Paramètre | Défaut | Rôle |
|---|---|---|
| `VoucherApiProvider` | obligatoire | fournisseur des vouchers ; disponible : `opnsense` |
| `OpnsenseUrl` | obligatoire (opnsense) | adresse de l'API |
| `OpnsenseApiKey` | obligatoire (opnsense) | clé de l'API |
| `OpnsenseApiSecret` | obligatoire (opnsense) | secret de l'API ; secret |
| `OpnsenseVoucherServer` | `wifipubauth` | nom du serveur Voucher |
| `VoucherGroup` | `sms` | groupe des vouchers |
| `VoucherValidity` | `12h` | durée d'accès, à partir de la première connexion |
| `VoucherExpiry` | `1h` | délai d'expiration d'un voucher inutilisé |

**SMS**

| Paramètre | Défaut | Rôle |
|---|---|---|
| `SmsApiProvider` | obligatoire | fournisseur SMS ; disponible : `ovh` |
| `OvhSmsUrl` | `https://www.ovh.com/cgi-bin/sms/http2sms.cgi` | adresse de l'API http2sms |
| `OvhSmsAccount` | obligatoire (ovh) | compte SMS |
| `OvhSmsLogin` | obligatoire (ovh) | utilisateur SMS |
| `OvhSmsPassword` | obligatoire (ovh) | mot de passe de l'utilisateur SMS ; secret |
| `OvhSmsSender` | obligatoire (ovh) | expéditeur |

**Limites**

| Paramètre | Défaut | Rôle |
|---|---|---|
| `LimitPerIdentifier` | `2/24h` | demandes par numéro |
| `LimitPerMac` | `3/1h` | demandes par appareil (adresse MAC) |
| `LimitGlobal` | `10/24h` | demandes pour l'ensemble du service |
| `HttpTimeout` | `10s` | délai maximal des appels aux fournisseurs |

**Limites par fournisseur**, facultatives, appliquées en plus des limites
générales au fournisseur en service :

| Paramètre | Défaut | Rôle |
|---|---|---|
| `OpnsenseLimitPerIdentifier`, `OpnsenseLimitPerMac`, `OpnsenseLimitGlobal` | aucune | vouchers OPNsense |
| `OvhSmsLimitPerIdentifier`, `OvhSmsLimitPerMac`, `OvhSmsLimitGlobal` | aucune | SMS OVHcloud |

Les limites sont des fenêtres glissantes tenues en mémoire : elles repartent à
zéro au redémarrage du service. Une demande n'est servie que si toutes les
limites applicables le permettent ; le journal indique la limite atteinte
(`WPA_RULE`, par exemple `ovh.global`).

Quand `VoucherApiProvider` ou `SmsApiProvider` n'est pas écrit, il est déduit
des paramètres propres au fournisseur présents dans la configuration.

### Mots-clés obsolètes

Encore acceptés, avec un avertissement (`wifipubauthctl check`, journal) :

| Ancien | Nouveau |
|---|---|
| `SmsUrl`, `SmsAccount`, `SmsLogin`, `SmsPassword`, `SmsSender` | `OvhSmsUrl`, `OvhSmsAccount`, `OvhSmsLogin`, `OvhSmsPassword`, `OvhSmsSender` |
| `VoucherProvider` | `OpnsenseVoucherServer` |
| `CatalogDirectory` | `templates.d/` |

---

## Templates des messages

| Dossier | Rôle |
|---|---|
| `/etc/wifipubauth/templates/` | templates livrés, remplacés à chaque mise à jour |
| `/etc/wifipubauth/templates.d/` | templates personnels, prioritaires, jamais remplacés |

Le template utilisé se nomme `FOURNISSEUR-CANAL.conf` d'après
`VoucherApiProvider` et le canal : `opnsense-sms.conf`.

Format : une ligne `Message.LANGUE "texte"` par langue.

Dans `templates.d/`, n'écrire que les langues à changer : pour les autres, le
texte du template livré reste utilisé.

Repères :

| Repère | Remplacé par |
|---|---|
| `{identifiant}` | identifiant du voucher (obligatoire) |
| `{mot_de_passe}` | mot de passe du voucher (obligatoire) |
| `{validite_heures}` | durée de validité, en heures |

Langue : celle de `Language`, sinon celle du système (`LANG` de
`/etc/default/locale`). Si le template ne la contient pas, `en-US` est utilisé
et `wifipubauthctl check` le signale.

Le template livré envoie le code sous la forme `{mot_de_passe}-{identifiant}`,
attendue par la page de connexion livrée. Exemple de personnalisation,
`/etc/wifipubauth/templates.d/opnsense-sms.conf` :

```
Message.fr-FR "Wi-Fi solidaire, code valable {validite_heures} h : {mot_de_passe}-{identifiant}"
```

Puis :

```
sudo wifipubauthctl check
sudo wifipubauthctl restart
```

Longueur : un SMS simple fait 160 caractères au plus. Compter 13 caractères
pour le code (mot de passe de 10, tiret, identifiant de 2, réglages du serveur
Voucher) et 2 ou 3 pour `{validite_heures}`.

---

## Commande wifipubauthctl

```
wifipubauthctl COMMANDE [ARGUMENTS] [--OPTIONS]
```

| Commande | Rôle |
|---|---|
| `status` | état résumé de wifipubauth et de FreeRADIUS ; état complet d'un service en défaut |
| `start`, `stop`, `restart` | démarre, arrête, redémarre wifipubauth |
| `enable`, `disable` | active (et démarre), désactive (et arrête) wifipubauth au démarrage |
| `check` | vérifie la configuration de wifipubauth, du template et de FreeRADIUS (lecture seule) |
| `set PARAMÈTRE [VALEUR] [--genkey]` | écrit un réglage dans `conf.d/` après contrôle |
| `genkey` | affiche une clé pour `FingerprintKey` |
| `version` | version installée |
| `help [COMMANDE]` | aide générale ou d'une commande |

`set` :

- écrit `Paramètre "valeur"` dans le fichier de `conf.d/` qui contient déjà ce
  paramètre (ou son ancien nom), sinon dans `conf.d/local.conf` ;
- refuse un secret passé en argument : sans `VALEUR`, la saisie est masquée et
  confirmée ;
- `--genkey`, avec `Pseudonymization on`, génère aussi `FingerprintKey`.

Codes de retour : 0 succès, 1 échec, 2 usage incorrect, 3 service en défaut
(`status`).

---

## Journal

Le service écrit dans journald par le protocole natif. Chaque événement porte :

| Champ | Contenu |
|---|---|
| `MESSAGE` | texte lisible |
| `PRIORITY` | 3 erreur, 4 avertissement, 6 information |
| `MESSAGE_ID` | identifiant stable du type d'événement |
| `SYSLOG_IDENTIFIER` | `wifipubauth` |
| `WPA_EVENT` | nom de l'événement |
| `WPA_*` | données de l'événement (`WPA_IDENTIFIER` ou `WPA_HID`, `WPA_VOUCHER`, `WPA_SMS_ID`, `WPA_DETAIL`…) |

Événements :

| `WPA_EVENT` | Niveau | Signification |
|---|---|---|
| `service_started`, `service_stopped` | info | démarrage (adresse, port, canaux, langue), arrêt |
| `request_accepted` | info | demande valide, mise en file d'envoi |
| `request_rejected` | info | demande ignorée (`WPA_REASON`) |
| `limit_reached` | info | limite atteinte (`WPA_RULE`) |
| `voucher_created` | info | voucher créé |
| `sms_sent` | info | SMS envoyé |
| `queue_full` | avertissement | file d'attente pleine |
| `config_deprecated` | avertissement | mot-clé obsolète |
| `template_warning` | avertissement | langue absente du template, texte obsolète |
| `voucher_failed` | erreur | échec de la création du voucher |
| `sms_failed` | erreur | échec de l'envoi du SMS |
| `internal_error` | erreur | erreur interne |

Exemples :

```
journalctl -u wifipubauth
journalctl -u wifipubauth -p warning
journalctl -u wifipubauth WPA_EVENT=sms_sent -o verbose
```

Par défaut, le numéro du destinataire est journalisé en clair
(`WPA_IDENTIFIER`), ce qui permet de rattacher une connexion à un numéro. Avec
`Pseudonymization on`, il est remplacé par une empreinte HMAC-SHA256
(`WPA_HID`). La durée de conservation du journal relève de la configuration de
journald et des obligations légales applicables.

Aucun secret n'est journalisé.

---

## Sécurité

- Le service n'ouvre jamais l'accès : il répond toujours par un refus. L'accès
  n'est accordé que par le serveur Voucher du portail captif.
- Le serveur virtuel FreeRADIUS n'admet qu'un client, le portail captif, exige
  Message-Authenticator (BlastRADIUS) et écarte les identifiants mal formés.
- Le service écoute par défaut sur `127.0.0.1`.
- Les saisies du visiteur sont validées par liste blanche ; les réponses des
  fournisseurs sont contrôlées avant usage.
- Compte système dédié, sans shell ; unité systemd durcie (`ProtectSystem=strict`,
  `ProtectHome`, `NoNewPrivileges`, filtrage des appels système…).
- Configuration lisible par `root` et le groupe `wifipubauth` seulement (0640).
- Secrets : jamais en argument de commande ni dans `install.conf` ; saisie
  masquée ; jamais affichés ni journalisés.
- Aucun caractère de contrôle n'est affiché par les outils.
- L'utilisateur d'API n'a que le privilège « Services: Captive Portal ».

---

## Fichiers installés

| Chemin | Contenu |
|---|---|
| `/usr/local/lib/wifipubauth/wifipubauth.py` | programme |
| `/usr/local/lib/wifipubauth/VERSION`, `MANIFEST` | version installée, empreintes des fichiers livrés |
| `/usr/local/sbin/wifipubauthctl` | commande de contrôle |
| `/etc/systemd/system/wifipubauth.service` | unité systemd |
| `/etc/wifipubauth/wifipubauth.conf` | configuration par défaut |
| `/etc/wifipubauth/conf.d/` | configuration personnelle |
| `/etc/wifipubauth/templates/` | templates livrés |
| `/etc/wifipubauth/templates.d/` | templates personnels |
| `/etc/freeradius/3.0/mods-available/wifipubauth` | module `rest` (lien dans `mods-enabled/`) |
| `/etc/freeradius/3.0/sites-available/wifipubauth.example` | modèle du serveur virtuel |
| `/etc/freeradius/3.0/sites-available/wifipubauth` | serveur virtuel, créé à la première installation (lien dans `sites-enabled/`) |

---

## Désinstallation

Il n'y a pas de script de désinstallation. Pour retirer l'application :

1. arrêter et désactiver le service (`wifipubauthctl disable`) ;
2. retirer les fichiers listés ci-dessus et les liens de FreeRADIUS dans
   `mods-enabled/` et `sites-enabled/` ;
3. retirer le compte système `wifipubauth` ;
4. côté portail captif, retirer le serveur RADIUS de la zone.

La configuration personnelle (`conf.d/`, `templates.d/`) contient des secrets :
la détruire de façon sûre si elle n'est pas conservée.

---

## Développement et tests

Arborescence du projet :

```
wifipubauth.py             service
wifipubauthctl             commande de contrôle
wifipubauth.conf.example   configuration par défaut
install.sh, install.conf   installation
templates/                 templates livrés
portail/opnsense/          page de connexion pour le portail captif d'OPNsense
freeradius/                module rest et modèle du serveur virtuel
systemd/                   unité
tests/                     tests (pytest)
VERSION, CHANGELOG.md      version et historique
```

Tests, sans accès réseau extérieur (faux fournisseurs locaux) :

```
python3 -m pytest -q tests
```

Versions : [Semantic Versioning](https://semver.org/lang/fr/) ; historique dans
`CHANGELOG.md` ([Keep a Changelog](https://keepachangelog.com/fr/1.1.0/)).
