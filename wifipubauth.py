#!/usr/bin/python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Service d'inscription au Wi-Fi public par SMS.

Rôle : FreeRADIUS (module rest, section authorize, corps JSON) transmet chaque
demande du portail captif. Le service répond toujours 401 (refus) : l'accès est
ouvert par le voucher, vérifié par le portail captif. Pour une demande valide
(identifiant = numéro de mobile français ou adresse e-mail, mot de passe =
marqueur de demande) et dans les limites, il demande en tâche de fond un voucher
au fournisseur configuré (VoucherApiProvider) et l'envoie au visiteur par SMS.

Dépendances : Python 3.11 au moins, bibliothèque standard seulement.
Configuration : format OpenSSH (mot-clé valeur, Include, première valeur
lue retenue), voir le fichier exemple livré.
Version et historique : fichiers VERSION et CHANGELOG.md du projet.

Usage :
  wifipubauth.py --config FICHIER            lance le service
  wifipubauth.py --config FICHIER --verifier vérifie la configuration
  wifipubauth.py --generer-cle               affiche une clé pour FingerprintKey
"""

import argparse
import base64
import collections
import glob
import hashlib
import hmac
import http.server
import json
import os
import queue
import re
import shlex
import signal
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

NOM = "wifipubauth"   # remplacé au lancement par le nom du programme installé
CORPS_MAX = 16384           # octets acceptés d'une requête de FreeRADIUS
FILE_MAX = 50               # demandes en attente d'envoi
CHEMIN_DEMANDE = "/demande"

# --- Journal : protocole natif de journald ------------------------------------------
#
# Sous systemd (sortie d'erreur reliée au journal, repérée par $JOURNAL_STREAM),
# chaque événement est envoyé au socket natif de journald avec des champs
# structurés : MESSAGE (texte lisible), PRIORITY (niveau syslog), MESSAGE_ID
# (identifiant stable du type d'événement), SYSLOG_IDENTIFIER, et les champs
# propres au service, préfixés WPA_ (WPA_EVENT, WPA_HID, WPA_VOUCHER...).
# Hors systemd (lancement à la main, tests), ou si l'envoi échoue, une ligne de
# texte est écrite sur la sortie d'erreur, précédée du niveau au format <N>.
# Référence : https://systemd.io/JOURNAL_NATIVE_PROTOCOL/ et systemd.journal-fields(7).
# Aucun secret n'est jamais journalisé ; tout caractère de contrôle est retiré.

SOCKET_JOURNAL = "/run/systemd/journal/socket"
PRIORITES = {"error": 3, "warning": 4, "info": 6}

# Texte lisible et identifiant stable (MESSAGE_ID) de chaque type d'événement.
EVENEMENTS = {
    "service_started":  ("service démarré", "ea91b529645143ffbbf274556f46d88e"),
    "service_stopped":  ("service arrêté", "5e987e9c7e8040ab93439a8ceb2a9b3d"),
    "request_rejected": ("demande ignorée", "4722ee7c7755451aa0f24eb4394bd4b9"),
    "limit_reached":    ("limite atteinte, demande ignorée", "cd1fca0d949b44f7bfbaff3c4b989ca1"),
    "queue_full":       ("file d'attente pleine, demande ignorée", "c9df2a88716047169fbbff54a38c6182"),
    "request_accepted": ("demande acceptée", "87dfe11cec754aab949f3567d267b596"),
    "voucher_created":  ("voucher créé", "4a23ac96efcf4df089ab4e06626aaffd"),
    "sms_sent":         ("voucher envoyé par SMS", "b3f0a50c9ec24dccb556c5334faf13aa"),
    "sms_failed":       ("échec de l'envoi du SMS", "a93f65328d49446780cc2a2af12903b3"),
    "voucher_failed":   ("échec de la création du voucher", "195cf6e877e04712b97e9250adceaa17"),
    "internal_error":   ("erreur interne", "9e99364e2872407496b5d62283af76ca"),
    "config_deprecated": ("mot-clé obsolète", "0c5f3a7e2b9d4e61a8f7c2d9e4b1a356"),
    "template_warning": ("template", "da37679bf51d4e9698609874a79871ed"),
}

CONTROLE = re.compile(r"[\x00-\x1f\x7f]")


def propre(valeur):
    return CONTROLE.sub("", str(valeur))[:512]


def vers_journald():
    """Vrai si la sortie d'erreur est reliée au journal (méthode de systemd)."""
    attendu = os.environ.get("JOURNAL_STREAM", "")
    try:
        dev, ino = (int(x) for x in attendu.split(":"))
        etat = os.fstat(sys.stderr.fileno())
    except (ValueError, OSError):
        return False
    return (etat.st_dev, etat.st_ino) == (dev, ino)


class Journal:
    def __init__(self):
        self.natif = None
        self.verrou = threading.Lock()
        if vers_journald():
            import socket
            try:
                self.natif = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
                self.natif.connect(SOCKET_JOURNAL)
            except OSError:
                self.natif = None

    @staticmethod
    def serialiser(champs):
        """Format natif : CLÉ=valeur\\n, ou forme à longueur préfixée si la valeur
        contient un saut de ligne (n'arrive pas ici, les valeurs sont nettoyées)."""
        morceaux = []
        for cle, valeur in champs.items():
            donnees = valeur.encode("utf-8")
            if b"\n" in donnees:
                morceaux.append(cle.encode() + b"\n" + len(donnees).to_bytes(8, "little") + donnees + b"\n")
            else:
                morceaux.append(cle.encode() + b"=" + donnees + b"\n")
        return b"".join(morceaux)

    def ecrire(self, evenement, niveau, champs):
        texte, message_id = EVENEMENTS.get(evenement, (evenement, None))
        details = " ".join("%s=%s" % (cle, propre(val)) for cle, val in champs.items())
        entree = {
            "MESSAGE": texte + (" (" + details + ")" if details else ""),
            "PRIORITY": str(PRIORITES.get(niveau, 6)),
            "SYSLOG_IDENTIFIER": NOM,
            "WPA_EVENT": evenement,
        }
        if message_id:
            entree["MESSAGE_ID"] = message_id
        for cle, val in champs.items():
            entree["WPA_" + re.sub(r"[^A-Z0-9_]", "_", cle.upper())] = propre(val)
        if self.natif is not None:
            try:
                with self.verrou:
                    self.natif.send(self.serialiser(entree))
                return
            except OSError:
                pass  # repli sur la sortie d'erreur
        with self.verrou:
            sys.stderr.write("<%s>%s %s\n" % (entree["PRIORITY"], evenement, propre(entree["MESSAGE"])))
            sys.stderr.flush()


JOURNAL = None


def journal(evenement, niveau="info", **champs):
    global JOURNAL
    if JOURNAL is None:
        JOURNAL = Journal()
    JOURNAL.ecrire(evenement, niveau, champs)


class ErreurConfig(Exception):
    pass


# --- Configuration au format OpenSSH -------------------------------------------------

DUREE = re.compile(r"^([1-9][0-9]{0,5})([smhd])$")
LIMITE = re.compile(r"^([1-9][0-9]{0,4})/([1-9][0-9]{0,5}[smhd])$")
SECONDES = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def duree(texte):
    m = DUREE.fullmatch(texte)
    if not m:
        raise ValueError("durée attendue, ex. 12h, 30m, 1d")
    return int(m.group(1)) * SECONDES[m.group(2)]


def limite(texte):
    m = LIMITE.fullmatch(texte)
    if not m:
        raise ValueError("limite attendue, ex. 2/24h")
    return int(m.group(1)), duree(m.group(2))


def langue(texte):
    if not re.fullmatch(r"[a-z]{2}-[A-Z]{2}", texte):
        raise ValueError("forme attendue : fr-FR")
    return texte


def liste_exemptes(texte):
    """Identifiants séparés par des virgules, normalisés comme les demandes."""
    exemptes = []
    for morceau in texte.split(","):
        cible = identifiant_valide(morceau.strip())
        if cible is None:
            raise ValueError("identifiant invalide : %s" % morceau.strip())
        exemptes.append(cible[1])
    return tuple(exemptes)


def texte_court(texte):
    if not texte or len(texte) > 256 or any(ord(c) < 32 or ord(c) == 127 for c in texte):
        raise ValueError("texte vide, trop long ou avec caractère de contrôle")
    return texte


def port(texte):
    valeur = int(texte)
    if not 1 <= valeur <= 65535:
        raise ValueError("port entre 1 et 65535")
    return valeur


def url(texte):
    morceaux = urllib.parse.urlsplit(texte)
    if morceaux.scheme not in ("http", "https") or not morceaux.netloc:
        raise ValueError("URL http:// ou https:// attendue")
    return texte.rstrip("/")


def fournisseur_voucher(texte):
    if texte.lower() not in FOURNISSEURS_VOUCHER:
        raise ValueError("fournisseur inconnu : %s (disponible : %s)"
                         % (texte, ", ".join(sorted(FOURNISSEURS_VOUCHER))))
    return texte.lower()


def fournisseur_sms(texte):
    if texte.lower() not in FOURNISSEURS_SMS:
        raise ValueError("fournisseur inconnu : %s (disponible : %s)" % (texte, ", ".join(sorted(FOURNISSEURS_SMS))))
    return texte.lower()


def on_off(texte):
    if texte.lower() not in ("on", "off"):
        raise ValueError("on ou off attendu")
    return texte.lower() == "on"


def liste_canaux(texte):
    canaux = texte.split(",")
    for canal in canaux:
        if canal not in ("sms",):
            raise ValueError("canal inconnu : %s (disponible : sms)" % canal)
    return tuple(canaux)


# mot-clé (minuscules) : (nom de l'attribut, conversion, défaut ; None = obligatoire)
MOTS_CLES = {
    "listenaddress":      ("adresse", texte_court, "127.0.0.1"),
    "listenport":         ("port", port, 8642),
    "requestmarker":      ("marqueur", texte_court, "demande"),
    "channels":           ("canaux", liste_canaux, ("sms",)),
    "language":           ("langue", langue, ""),
    "pseudonymization":   ("pseudonymisation", on_off, False),
    "fingerprintkey":     ("cle_empreinte", texte_court, ""),
    "voucherapiprovider": ("voucher_fournisseur", fournisseur_voucher, None),
    "opnsenseurl":        ("opnsense_url", url, None),
    "opnsenseapikey":     ("opnsense_cle", texte_court, None),
    "opnsenseapisecret":  ("opnsense_secret", texte_court, None),
    "opnsensevoucherserver": ("voucher_serveur", texte_court, None),
    "vouchergroup":       ("voucher_groupe", texte_court, "sms"),
    "vouchervalidity":    ("voucher_validite", duree, 43200),
    "voucherexpiry":      ("voucher_expiration", duree, 3600),
    "smsapiprovider":     ("sms_fournisseur", fournisseur_sms, None),
    "ovhsmsurl":          ("ovh_sms_url", url, "https://www.ovh.com/cgi-bin/sms/http2sms.cgi"),
    "ovhsmsaccount":      ("ovh_sms_compte", texte_court, None),
    "ovhsmslogin":        ("ovh_sms_login", texte_court, None),
    "ovhsmspassword":     ("ovh_sms_mot_de_passe", texte_court, None),
    "ovhsmssender":       ("ovh_sms_expediteur", texte_court, None),
    "limitperidentifier": ("limite_identifiant", limite, (2, 86400)),
    "limitpermac":        ("limite_mac", limite, (3, 3600)),
    "limitglobal":        ("limite_globale", limite, (10, 86400)),
    "opnsenselimitperidentifier": ("limite_opnsense_identifiant", limite, ()),
    "opnsenselimitpermac":        ("limite_opnsense_mac", limite, ()),
    "opnsenselimitglobal":        ("limite_opnsense_globale", limite, ()),
    "ovhsmslimitperidentifier":   ("limite_ovh_identifiant", limite, ()),
    "ovhsmslimitpermac":          ("limite_ovh_mac", limite, ()),
    "ovhsmslimitglobal":          ("limite_ovh_globale", limite, ()),
    "httptimeout":        ("delai_http", duree, 10),
    "limitexemptidentifiers": ("exemptes", liste_exemptes, ()),
}

# Nom affiché de chaque mot-clé.
NOMS = {
    "listenaddress": "ListenAddress", "listenport": "ListenPort", "requestmarker": "RequestMarker",
    "channels": "Channels", "language": "Language",
    "pseudonymization": "Pseudonymization", "fingerprintkey": "FingerprintKey",
    "voucherapiprovider": "VoucherApiProvider",
    "opnsenseurl": "OpnsenseUrl", "opnsenseapikey": "OpnsenseApiKey",
    "opnsenseapisecret": "OpnsenseApiSecret", "opnsensevoucherserver": "OpnsenseVoucherServer",
    "vouchergroup": "VoucherGroup", "vouchervalidity": "VoucherValidity",
    "voucherexpiry": "VoucherExpiry", "smsapiprovider": "SmsApiProvider",
    "ovhsmsurl": "OvhSmsUrl", "ovhsmsaccount": "OvhSmsAccount", "ovhsmslogin": "OvhSmsLogin",
    "ovhsmspassword": "OvhSmsPassword", "ovhsmssender": "OvhSmsSender",
    "limitperidentifier": "LimitPerIdentifier", "limitpermac": "LimitPerMac",
    "limitglobal": "LimitGlobal", "httptimeout": "HttpTimeout",
    "limitexemptidentifiers": "LimitExemptIdentifiers",
    "opnsenselimitperidentifier": "OpnsenseLimitPerIdentifier", "opnsenselimitpermac": "OpnsenseLimitPerMac",
    "opnsenselimitglobal": "OpnsenseLimitGlobal", "ovhsmslimitperidentifier": "OvhSmsLimitPerIdentifier",
    "ovhsmslimitpermac": "OvhSmsLimitPerMac", "ovhsmslimitglobal": "OvhSmsLimitGlobal",
}

# Mots-clés secrets : jamais affichés.
SECRETS = {"opnsenseapisecret", "ovhsmspassword", "fingerprintkey"}

# Mots-clés obligatoires selon le fournisseur choisi :
# mot-clé du choix → {fournisseur: mots-clés exigés}.
OBLIGATOIRES_FOURNISSEUR = {
    "voucherapiprovider": {
        "opnsense": ("opnsenseurl", "opnsenseapikey", "opnsenseapisecret", "opnsensevoucherserver"),
    },
    "smsapiprovider": {
        "ovh": ("ovhsmsaccount", "ovhsmslogin", "ovhsmspassword", "ovhsmssender"),
    },
}

# Limites propres à chaque fournisseur, facultatives, en plus des limites
# générales : fournisseur → attributs (par identifiant, par MAC, globale).
LIMITES_FOURNISSEUR = {
    "opnsense": ("limite_opnsense_identifiant", "limite_opnsense_mac", "limite_opnsense_globale"),
    "ovh": ("limite_ovh_identifiant", "limite_ovh_mac", "limite_ovh_globale"),
}

# Anciens mots-clés encore acceptés (avertissement) : ancien → nouveau.
ALIAS = {
    "voucherprovider": "opnsensevoucherserver",
    "smsurl": "ovhsmsurl", "smsaccount": "ovhsmsaccount", "smslogin": "ovhsmslogin",
    "smspassword": "ovhsmspassword", "smssender": "ovhsmssender",
}

# Anciens mots-clés sans équivalent direct, encore lus (avertissement) :
# mot-clé → (nom affiché, remplacement indiqué).
OBSOLETES = {"catalogdirectory": ("CatalogDirectory", "templates.d/")}

# Fournisseur impliqué par un ancien mot-clé, en l'absence de choix explicite.
FOURNISSEUR_IMPLICITE = {
    "voucherprovider": ("voucherapiprovider", "opnsense"),
    "smsurl": ("smsapiprovider", "ovh"), "smsaccount": ("smsapiprovider", "ovh"),
    "smslogin": ("smsapiprovider", "ovh"), "smspassword": ("smsapiprovider", "ovh"),
    "smssender": ("smsapiprovider", "ovh"),
}


# Mots-clés propres à chaque fournisseur, sans valeur par défaut : leur présence
# désigne le fournisseur quand le choix n'est pas écrit.
MARQUEURS_FOURNISSEUR = {
    "voucherapiprovider": {"opnsense": ("opnsenseurl", "opnsenseapikey", "opnsenseapisecret")},
    "smsapiprovider": {"ovh": ("ovhsmsaccount", "ovhsmslogin", "ovhsmspassword", "ovhsmssender")},
}


class Config:
    pass


def lire_valeurs(chemin, obsoletes=None):
    """Lit le fichier et ses Include ; pour chaque mot-clé, la première valeur lue
    l'emporte. Renvoie {mot-clé en minuscules: valeur convertie}, sans exiger les
    mots-clés obligatoires (sert aussi à install.sh et wifipubauthctl). Les anciens
    mots-clés rencontrés sont ajoutés à obsoletes : (ancien, nouveau)."""
    valeurs = {}
    implicites = []
    if obsoletes is None:
        obsoletes = []

    def lire(fichier, profondeur):
        if profondeur > 4:
            raise ErreurConfig("%s : Include imbriqués trop profondément" % fichier)
        try:
            with open(fichier, encoding="utf-8") as f:
                lignes = f.readlines()
        except OSError as e:
            raise ErreurConfig("%s : illisible (%s)" % (fichier, e.strerror))
        for numero, ligne in enumerate(lignes, 1):
            ligne = ligne.strip()
            if not ligne or ligne.startswith("#"):
                continue
            try:
                morceaux = shlex.split(ligne, comments=True, posix=True)
            except ValueError:
                raise ErreurConfig("%s:%d : guillemets non fermés" % (fichier, numero))
            if not morceaux:
                continue
            mot, arguments = morceaux[0].lower(), morceaux[1:]
            if mot == "include":
                if len(arguments) != 1:
                    raise ErreurConfig("%s:%d : Include attend un seul chemin" % (fichier, numero))
                for inclus in sorted(glob.glob(arguments[0])):
                    lire(inclus, profondeur + 1)
                continue
            if mot in OBSOLETES:
                if len(arguments) != 1:
                    raise ErreurConfig("%s:%d : %s attend une seule valeur" % (fichier, numero, morceaux[0]))
                if (OBSOLETES[mot][1], morceaux[0]) not in obsoletes:
                    obsoletes.append((OBSOLETES[mot][1], morceaux[0]))
                valeurs.setdefault(mot, arguments[0])
                continue
            if mot in ALIAS:
                if (NOMS[ALIAS[mot]], morceaux[0]) not in obsoletes:
                    obsoletes.append((NOMS[ALIAS[mot]], morceaux[0]))
                implicites.append(FOURNISSEUR_IMPLICITE[mot])
                mot = ALIAS[mot]
            if mot not in MOTS_CLES:
                raise ErreurConfig("%s:%d : mot-clé inconnu « %s »" % (fichier, numero, morceaux[0]))
            if len(arguments) != 1:
                raise ErreurConfig("%s:%d : %s attend une seule valeur" % (fichier, numero, morceaux[0]))
            if mot in valeurs:
                continue  # première valeur obtenue retenue, comme sshd
            nom, conversion, _ = MOTS_CLES[mot]
            try:
                valeurs[mot] = conversion(arguments[0])
            except ValueError as e:
                raise ErreurConfig("%s:%d : %s : %s" % (fichier, numero, morceaux[0], e))

    lire(chemin, 0)
    for choix, fournisseur in implicites:
        valeurs.setdefault(choix, fournisseur)
    for choix, parfournisseur in MARQUEURS_FOURNISSEUR.items():
        if choix not in valeurs:
            trouves = [f for f, mots in parfournisseur.items() if any(m in valeurs for m in mots)]
            if len(trouves) == 1:
                valeurs[choix] = trouves[0]
    return valeurs


def lire_config(chemin):
    """Configuration complète : valeurs lues, défauts, mots-clés obligatoires vérifiés."""
    obsoletes = []
    valeurs = lire_valeurs(chemin, obsoletes)
    config = Config()
    config.obsoletes = obsoletes
    config.dossier_conf = os.path.dirname(os.path.abspath(chemin))
    config.dossier_catalogues = valeurs.get("catalogdirectory", "/etc/%s/i18n" % NOM)
    for mot, (nom, _, defaut) in MOTS_CLES.items():
        setattr(config, nom, valeurs.get(mot, defaut))
    manquants = mots_cles_manquants(valeurs)
    if manquants:
        raise ErreurConfig("mots-clés obligatoires absents : %s" % ", ".join(manquants))
    if config.pseudonymisation and not config.cle_empreinte:
        raise ErreurConfig("Pseudonymization on sans FingerprintKey "
                           "(%sctl set pseudonymization on --genkey)" % NOM)
    return config


def mots_cles_manquants(valeurs):
    """Mots-clés obligatoires absents, selon les canaux et les fournisseurs."""
    conditionnels = {m for parchoix in OBLIGATOIRES_FOURNISSEUR.values()
                     for liste in parchoix.values() for m in liste}
    exiges = [m for m, (_, _, d) in MOTS_CLES.items() if d is None and m not in conditionnels]
    canaux = valeurs.get("channels", MOTS_CLES["channels"][2])
    if "sms" not in canaux:
        exiges.remove("smsapiprovider")
    for choix, parchoix in OBLIGATOIRES_FOURNISSEUR.items():
        if choix in exiges:
            exiges += parchoix.get(valeurs.get(choix), ())
    return [NOMS[m] for m in exiges if m not in valeurs]


# --- Templates des messages ---------------------------------------------------------

# Textes livrés par les versions 0.8 et antérieures (i18n/LANGUE.json, clé
# sms.voucher) : une copie identique n'est pas une personnalisation.
TEXTES_ANCIENS = {
    "Code Wi-Fi valable {validite_heures} h : identifiant {identifiant}, mot de passe {mot_de_passe}",
    "Wi-Fi code valid for {validite_heures} h: username {identifiant}, password {mot_de_passe}",
}


def langue_systeme():
    """Langue du système (LANG de /etc/default/locale, /etc/locale.conf ou de
    l'environnement), forme fr-FR ; None si indéterminée (C, POSIX)."""
    valeur = None
    for fichier in ("/etc/default/locale", "/etc/locale.conf"):
        try:
            with open(fichier, encoding="utf-8") as f:
                for ligne in f:
                    cle, _, v = ligne.strip().partition("=")
                    if cle == "LANG":
                        valeur = v.strip().strip('"\'')
        except OSError:
            continue
        if valeur:
            break
    valeur = valeur or os.environ.get("LANG", "")
    m = re.match(r"([a-z]{2})_([A-Z]{2})", valeur)
    return "%s-%s" % m.groups() if m else None


def lire_modele(config, canal="sms"):
    """Texte du message pour le canal : template FOURNISSEUR-CANAL.conf,
    templates.d/ prioritaire sur templates/, mot-clé Message.LANGUE.
    Renvoie (texte, langue retenue, avertissements)."""
    nom = "%s-%s.conf" % (config.voucher_fournisseur, canal)
    avertissements = []
    messages = {}
    lus = []
    for dossier in ("templates.d", "templates"):
        chemin = os.path.join(config.dossier_conf, dossier, nom)
        if not os.path.exists(chemin):
            continue
        lus.append(chemin)
        try:
            with open(chemin, encoding="utf-8") as f:
                lignes = f.readlines()
        except OSError as e:
            raise ErreurConfig("%s : illisible (%s)" % (chemin, e.strerror))
        for numero, ligne in enumerate(lignes, 1):
            if not ligne.strip() or ligne.strip().startswith("#"):
                continue
            try:
                morceaux = shlex.split(ligne, comments=True, posix=True)
            except ValueError:
                raise ErreurConfig("%s:%d : guillemets non fermés" % (chemin, numero))
            if not morceaux:
                continue
            m = re.fullmatch(r"message\.([a-z]{2})-([a-z]{2})", morceaux[0].lower())
            if not m or len(morceaux) != 2:
                raise ErreurConfig("%s:%d : « Message.fr-FR \"texte\" » attendu" % (chemin, numero))
            messages.setdefault("%s-%s" % (m.group(1), m.group(2).upper()), (morceaux[1], chemin))
    if not lus:
        raise ErreurConfig("template %s absent (%s/templates)" % (nom, config.dossier_conf))

    voulue = config.langue or langue_systeme() or "en-US"
    retenue = voulue if voulue in messages else "en-US" if "en-US" in messages else None
    if retenue is None:
        raise ErreurConfig("template %s : ni Message.%s ni Message.en-US" % (nom, voulue))
    if retenue != voulue:
        avertissements.append("template %s : Message.%s absent, en-US utilisé" % (nom, voulue))
    texte, chemin = messages[retenue]

    # Texte personnalisé en 0.8 (CatalogDirectory, sms.voucher), lu tant que
    # templates.d/ ne le remplace pas.
    if canal == "sms" and not chemin.startswith(os.path.join(config.dossier_conf, "templates.d")):
        ancien = os.path.join(config.dossier_catalogues, retenue + ".json")
        try:
            with open(ancien, encoding="utf-8") as f:
                texte_ancien = json.load(f).get("sms.voucher")
        except (OSError, ValueError, AttributeError):
            texte_ancien = None
        if isinstance(texte_ancien, str) and texte_ancien not in TEXTES_ANCIENS:
            texte, chemin = texte_ancien, ancien
            avertissements.append("texte obsolète : %s (sms.voucher), remplacé par %s"
                                  % (ancien, os.path.join(config.dossier_conf, "templates.d", nom)))

    for repere in ("{identifiant}", "{mot_de_passe}"):
        if repere not in texte:
            raise ErreurConfig("%s : le texte doit contenir %s" % (chemin, repere))
    if any(ord(c) < 32 or ord(c) == 127 for c in texte):
        raise ErreurConfig("%s : caractère de contrôle dans le texte" % chemin)
    return texte, retenue, avertissements


# --- Validation des saisies du visiteur ---------------------------------------------

NUMERO = re.compile(r"^0([67][0-9]{8})$")
EMAIL = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}(\.[A-Za-z0-9-]{1,63})*\.[A-Za-z]{2,63}$")
MAC = re.compile(r"^[0-9a-f]{2}([:-][0-9a-f]{2}){5}$")


def identifiant_valide(texte):
    """Renvoie (canal, destinataire) ou None. Liste blanche stricte."""
    if not isinstance(texte, str) or len(texte) > 254:
        return None
    numero = texte.replace(" ", "").replace(".", "")
    m = NUMERO.fullmatch(numero)
    if m:
        return "sms", "0033" + m.group(1)
    if EMAIL.fullmatch(texte):
        local, domaine = texte.rsplit("@", 1)
        return "email", local + "@" + domaine.lower()
    return None


def mac_normalisee(texte):
    if isinstance(texte, str) and MAC.fullmatch(texte.lower()):
        return texte.lower().replace("-", ":")
    return None


# --- Limites en mémoire (fenêtres glissantes) ---------------------------------------


class Limites:
    def __init__(self, config):
        """Règles : limites générales (identifier, mac, global), puis limites des
        fournisseurs en service (FOURNISSEUR.identifier, .mac, .global)."""
        self.regles = {
            "identifier": config.limite_identifiant,
            "mac": config.limite_mac,
            "global": config.limite_globale,
        }
        for fournisseur in (config.voucher_fournisseur, config.sms_fournisseur):
            for portee, attribut in zip(("identifier", "mac", "global"), LIMITES_FOURNISSEUR.get(fournisseur, ())):
                if getattr(config, attribut):
                    self.regles["%s.%s" % (fournisseur, portee)] = getattr(config, attribut)
        self.historique = collections.defaultdict(collections.deque)
        self.verrou = threading.Lock()

    def accepter(self, cles):
        """cles : {regle: clé}. Tout ou rien : consomme seulement si toutes passent."""
        maintenant = time.monotonic()
        with self.verrou:
            for regle, cle in cles.items():
                nombre, fenetre = self.regles[regle]
                file_ = self.historique[(regle, cle)]
                while file_ and file_[0] <= maintenant - fenetre:
                    file_.popleft()
                if len(file_) >= nombre:
                    return regle
            for regle, cle in cles.items():
                self.historique[(regle, cle)].append(maintenant)
            # ménage des entrées vides
            for cle in [c for c, f in self.historique.items() if not f]:
                del self.historique[cle]
        return None


# --- Appels sortants ---------------------------------------------------------------


def creer_voucher(config):
    """Demande un voucher au fournisseur configuré (VoucherApiProvider).
    Renvoie (identifiant, mot de passe)."""
    return FOURNISSEURS_VOUCHER[config.voucher_fournisseur](config)


def creer_voucher_opnsense(config):
    """API generateVouchers d'OPNsense. Renvoie (identifiant, mot de passe)."""
    adresse = "%s/api/captiveportal/voucher/generateVouchers/%s" % (
        config.opnsense_url, urllib.parse.quote(config.voucher_serveur, safe=""))
    corps = json.dumps({
        "count": 1,
        "validity": config.voucher_validite,
        "expirytime": config.voucher_expiration,
        "vouchergroup": config.voucher_groupe,
    }).encode()
    jeton = base64.b64encode(("%s:%s" % (config.opnsense_cle, config.opnsense_secret)).encode()).decode()
    requete = urllib.request.Request(adresse, data=corps, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": "Basic " + jeton,
    })
    with urllib.request.urlopen(requete, timeout=config.delai_http) as reponse:
        donnees = json.loads(reponse.read(65536))
    if not (isinstance(donnees, list) and len(donnees) == 1 and isinstance(donnees[0], dict)):
        raise RuntimeError("opnsense_bad_response")
    identifiant, mot_de_passe = donnees[0].get("username"), donnees[0].get("password")
    for valeur in (identifiant, mot_de_passe):
        if not (isinstance(valeur, str) and 0 < len(valeur) <= 64
                and all(33 <= ord(c) <= 126 for c in valeur)):
            raise RuntimeError("opnsense_bad_voucher")
    return identifiant, mot_de_passe


# Fournisseurs de vouchers disponibles (VoucherApiProvider) : nom → fonction.
FOURNISSEURS_VOUCHER = {"opnsense": creer_voucher_opnsense}


def envoyer_sms(config, destinataire, message):
    """Envoi par le fournisseur configuré (SmsApiProvider). Renvoie l'identifiant du SMS."""
    return FOURNISSEURS_SMS[config.sms_fournisseur](config, destinataire, message)


def envoyer_sms_ovh(config, destinataire, message):
    """Envoi http2sms d'OVHcloud. Renvoie l'identifiant du SMS."""
    corps = urllib.parse.urlencode({
        "account": config.ovh_sms_compte,
        "login": config.ovh_sms_login,
        "password": config.ovh_sms_mot_de_passe,
        "from": config.ovh_sms_expediteur,
        "to": destinataire,
        "message": message,
        "noStop": "1",
        "contentType": "text/json",
    }).encode()
    requete = urllib.request.Request(config.ovh_sms_url, data=corps, method="POST", headers={
        "Content-Type": "application/x-www-form-urlencoded",
    })
    with urllib.request.urlopen(requete, timeout=config.delai_http) as reponse:
        donnees = json.loads(reponse.read(65536))
    statut = donnees.get("status") if isinstance(donnees, dict) else None
    if not (isinstance(statut, int) and 100 <= statut < 200):
        raise RuntimeError("sms_status_%s" % (statut if isinstance(statut, int) else "invalid"))
    identifiants = donnees.get("SmsIds") or donnees.get("smsIds") or []
    return str(identifiants[0]) if identifiants else ""


# Fournisseurs SMS disponibles (SmsApiProvider) : nom → fonction d'envoi.
FOURNISSEURS_SMS = {"ovh": envoyer_sms_ovh}


# --- Traitement ----------------------------------------------------------------------


class Service:
    def __init__(self, config, gabarit):
        self.config = config
        self.gabarit = gabarit
        self.limites = Limites(config)
        self.file = queue.Queue(maxsize=FILE_MAX)

    def empreinte(self, texte):
        """Texte tel quel, ou son empreinte HMAC-SHA256 si Pseudonymization est on."""
        if not self.config.pseudonymisation:
            return texte
        return hmac.new(self.config.cle_empreinte.encode(), texte.encode(), hashlib.sha256).hexdigest()[:16]

    def reference(self, destinataire):
        """Champ du journal désignant le destinataire : en clair (identifier) par
        défaut, ou empreinte (hid) si Pseudonymization est on."""
        if self.config.pseudonymisation:
            return {"hid": self.empreinte(destinataire)}
        return {"identifier": destinataire}

    def demande(self, attributs):
        """Traite une requête de FreeRADIUS. Ne renvoie rien : la réponse est toujours 401."""
        def valeur(nom):
            entree = attributs.get(nom)
            if isinstance(entree, dict) and isinstance(entree.get("value"), list) and entree["value"]:
                v = entree["value"][0]
                return v if isinstance(v, str) else None
            return None

        mot_de_passe = valeur("User-Password")
        if mot_de_passe is None or not hmac.compare_digest(
                mot_de_passe.encode(), self.config.marqueur.encode()):
            journal("request_rejected", reason="not_a_request")
            return
        cible = identifiant_valide(valeur("User-Name"))
        if cible is None:
            journal("request_rejected", reason="invalid_identifier")
            return
        canal, destinataire = cible
        ref = self.reference(destinataire)
        if canal not in self.config.canaux:
            journal("request_rejected", reason="channel_unavailable", channel=canal, **ref)
            return
        mac = mac_normalisee(valeur("Calling-Station-Id"))
        exempte = destinataire in self.config.exemptes
        valeurs = {} if exempte else {"identifier": self.empreinte(destinataire), "global": "*"}
        if mac and not exempte:
            valeurs["mac"] = self.empreinte(mac)
        cles = {regle: valeurs[regle.rsplit(".", 1)[-1]] for regle in self.limites.regles
                if regle.rsplit(".", 1)[-1] in valeurs}
        regle = self.limites.accepter(cles)
        if regle:
            journal("limit_reached", rule=regle, **ref)
            return
        try:
            self.file.put_nowait((canal, destinataire, ref))
        except queue.Full:
            journal("queue_full", "warning", **ref)
            return
        journal("request_accepted", channel=canal, **ref, mac_known=bool(mac),
                **({"limit_exempt": True} if exempte else {}))

    def travailleur(self):
        while True:
            canal, destinataire, ref = self.file.get()
            try:
                self.traiter(canal, destinataire, ref)
            except Exception as e:  # le travailleur ne doit jamais mourir
                journal("internal_error", "error", **ref, detail=type(e).__name__)
            finally:
                self.file.task_done()

    def traiter(self, canal, destinataire, ref):
        try:
            identifiant, mot_de_passe = creer_voucher(self.config)
        except (urllib.error.URLError, OSError, ValueError, RuntimeError) as e:
            journal("voucher_failed", "error", **ref, detail=raison(e))
            return
        journal("voucher_created", **ref, voucher=identifiant, group=self.config.voucher_groupe)
        message = (self.gabarit
                   .replace("{identifiant}", identifiant)
                   .replace("{mot_de_passe}", mot_de_passe)
                   .replace("{validite_heures}", str(self.config.voucher_validite // 3600)))
        try:
            sms_id = envoyer_sms(self.config, destinataire, message)
        except (urllib.error.URLError, OSError, ValueError, RuntimeError) as e:
            journal("sms_failed", "error", **ref, voucher=identifiant, detail=raison(e))
            return
        journal("sms_sent", **ref, voucher=identifiant, sms_id=sms_id)


def raison(erreur):
    if isinstance(erreur, urllib.error.HTTPError):
        return "http_%d" % erreur.code
    if isinstance(erreur, RuntimeError):
        return str(erreur)
    return type(erreur).__name__


# --- Serveur HTTP local ---------------------------------------------------------------


def fabrique_gestionnaire(service):
    class Gestionnaire(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = NOM
        sys_version = ""

        def refuser(self, code=401):
            self.send_response(code)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_POST(self):
            if self.path != CHEMIN_DEMANDE:
                return self.refuser(404)
            try:
                taille = int(self.headers.get("Content-Length", "-1"))
            except ValueError:
                taille = -1
            if not 0 < taille <= CORPS_MAX:
                self.close_connection = True
                journal("request_rejected", reason="bad_length")
                return self.refuser(401)
            corps = self.rfile.read(taille)
            try:
                attributs = json.loads(corps)
                if not isinstance(attributs, dict):
                    raise ValueError
            except ValueError:
                journal("request_rejected", reason="bad_json")
                return self.refuser(401)
            service.demande(attributs)
            self.refuser(401)

        def do_GET(self):
            self.refuser(405)

        def log_message(self, *args):
            pass  # le journal applicatif suffit

    return Gestionnaire


def nom_du_programme():
    """Nom du programme installé, qui est le nom du service (install.sh le
    copie sous NOM_SERVICE.py) : sert au nom du processus et au journal."""
    nom = os.path.basename(sys.argv[0])
    return nom[:-3] if nom.endswith(".py") else nom


def nommer_processus():
    """Nom du processus affiché par ss, ps et top, tronqué à 15 caractères
    par le noyau."""
    try:
        with open("/proc/self/comm", "w") as f:
            f.write(NOM[:15])
    except OSError:
        pass  # sans conséquence : le nom reste python3


def main():
    analyse = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    analyse.add_argument("--config")
    analyse.add_argument("--verifier", action="store_true",
                         help="vérifie la configuration et le template, puis s'arrête")
    analyse.add_argument("--generer-cle", action="store_true",
                         help="affiche une clé aléatoire de 256 bits pour FingerprintKey, puis s'arrête")
    options = analyse.parse_args()
    if options.generer_cle:
        import secrets
        print(secrets.token_hex(32))
        return 0
    if not options.config:
        analyse.error("--config est obligatoire")
    global NOM, JOURNAL
    NOM = nom_du_programme() or NOM
    try:
        config = lire_config(options.config)
        gabarit, langue_retenue, avertissements = lire_modele(config)
    except ErreurConfig as e:
        texte = "".join(c if c.isprintable() else "?" for c in str(e))
        print("configuration invalide : %s" % texte, file=sys.stderr)
        return 2
    for nouveau, ancien in config.obsoletes:
        print("mot-clé obsolète : %s, remplacé par %s" % (ancien, nouveau), file=sys.stderr)
    for avertissement in avertissements:
        print("".join(c if c.isprintable() else "?" for c in avertissement), file=sys.stderr)
    if options.verifier:
        print("configuration valide", file=sys.stderr)
        return 0

    nommer_processus()
    JOURNAL = Journal()   # avant tout fil : choix natif ou sortie d'erreur fait une fois
    for nouveau, ancien in config.obsoletes:
        journal("config_deprecated", "warning", keyword=ancien, replacement=nouveau)
    for avertissement in avertissements:
        journal("template_warning", "warning", detail=avertissement)
    service = Service(config, gabarit)
    threading.Thread(target=service.travailleur, daemon=True).start()
    serveur = http.server.ThreadingHTTPServer((config.adresse, config.port), fabrique_gestionnaire(service))
    serveur.daemon_threads = True

    def arret(signum, frame):
        threading.Thread(target=serveur.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, arret)
    signal.signal(signal.SIGINT, arret)
    journal("service_started", address=config.adresse, port=config.port, channels=",".join(config.canaux),
            language=langue_retenue)
    serveur.serve_forever()
    serveur.server_close()
    journal("service_stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
