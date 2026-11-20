#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# install.sh — installation et mise à jour de wifipubauth : inscription au Wi-Fi
# public par SMS, derrière un portail captif (FreeRADIUS, vouchers).
#
# Paramètres : install.conf, à côté de ce script, à compléter avant la première
# installation (secrets exceptés : ils sont demandés). Lors d'une mise à jour,
# install.conf n'est exigé que pour les nouveaux paramètres obligatoires. Après
# usage, il est renommé install.conf.old (ou fusionné dans celui-ci).
#
# Déroulement :
#   1. Vérifications : rien n'est modifié, seuls les problèmes sont affichés.
#   2. Plan : notes de version (mise à jour), paquets, compte, dossiers et
#      fichiers ajoutés ou modifiés, fichiers modifiés localement (sauvegardés
#      puis remplacés), configuration rendue obsolète et ce qui la remplace.
#      Rien n'est fait sans validation : on peut annuler à ce stade.
#   3. Installation standard : paquets, compte, dossiers, fichiers livrés.
#      FreeRADIUS reste arrêté tant qu'il n'est pas configuré (Debian le
#      démarre avec une configuration d'exemple, secret public testing123).
#   4. Services : démarrage ou redémarrage de wifipubauth et de FreeRADIUS,
#      chacun seulement si sa configuration est complète et valide.
#   5. Bilan : ce qui reste à configurer, et comment.
#
# Configuration : la configuration par défaut est remplacée à chaque passage ;
# les paramètres d'install.conf et les secrets sont écrits dans conf.d/ par
# wifipubauthctl set. Le serveur virtuel FreeRADIUS est créé une seule fois.
#
# Seuls les ajouts et modifications sont affichés. Le détail complet (dont la
# sortie d'apt) est écrit dans install.log, à côté de ce script ; aucun secret
# n'y figure.
#
# Usage : sudo ./install.sh [--simulation] [--oui] [--aide]
#   --simulation  vérifications et plan seulement, rien n'est modifié
#   --oui         répond oui aux confirmations (les saisies restent demandées)
#
# Dépendances : bash, apt-get, dpkg-query, dpkg, getent, useradd, install,
# stat, cmp, mktemp, runuser, sha256sum, python3 ; systemctl.
# Aide et signalement de bugs : https://github.com/zobrak/wifibpub
# Version et historique : fichiers VERSION et CHANGELOG.md du projet, à côté
# de ce script (aucun numéro de version en dur ici).

set -Eeuo pipefail
umask 022

DOSSIER_SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly DOSSIER_SCRIPT
readonly NOM_PROJET="wifipubauth"            # nom d'origine dans les fichiers livrés

# Valeur active (ligne sans #) d'un mot-clé dans un fichier « MotClé valeur ».
valeur_conf() {
    local fichier="$1" cle="$2" mot valeur
    [[ -r "$fichier" ]] || return 0
    while read -r mot valeur || [[ -n "$mot" ]]; do
        [[ -n "$mot" && "$mot" != \#* && "${mot,,}" == "${cle,,}" && -n "$valeur" ]] || continue
        valeur="${valeur%%#*}"; valeur="${valeur%"${valeur##*[![:space:]]}"}"
        valeur="${valeur#\"}"; valeur="${valeur%\"}"
        printf '%s' "$valeur"
        return 0
    done < "$fichier"
    return 0
}

# Nom du service (compte système, dossiers, commandes) : ServiceName
# d'install.conf, sinon d'install.conf.old, sinon le nom du projet.
NOM_SERVICE="$(valeur_conf "$DOSSIER_SCRIPT/install.conf" ServiceName)"
[[ -n "$NOM_SERVICE" ]] || NOM_SERVICE="$(valeur_conf "$DOSSIER_SCRIPT/install.conf.old" ServiceName)"
[[ -n "$NOM_SERVICE" ]] || NOM_SERVICE="$NOM_PROJET"
readonly NOM_SERVICE

readonly COMPTE="$NOM_SERVICE"
readonly DOSSIER_CONF="/etc/$NOM_SERVICE"
readonly DOSSIER_SURCHARGES="$DOSSIER_CONF/conf.d"
readonly DOSSIER_CODE="/usr/local/lib/$NOM_SERVICE"
readonly PROGRAMME="$DOSSIER_CODE/$NOM_SERVICE.py"
readonly CONF="$DOSSIER_CONF/$NOM_SERVICE.conf"
readonly UNITE="/etc/systemd/system/$NOM_SERVICE.service"
readonly CTL="/usr/local/sbin/${NOM_SERVICE}ctl"
readonly VERSION_INSTALLEE="$DOSSIER_CODE/VERSION"
readonly MANIFESTE="$DOSSIER_CODE/MANIFEST"
readonly DOSSIER_FR="/etc/freeradius/3.0"
readonly FR_MODELE="$DOSSIER_FR/sites-available/$NOM_SERVICE.example"
readonly FR_MODULE="$DOSSIER_FR/mods-available/$NOM_SERVICE"
readonly PAQUETS=(freeradius freeradius-rest freeradius-utils python3 ca-certificates)
readonly FREERADIUS_MIN="3.2.7"   # version vérifiée pour le contrat du module rest
readonly PYTHON_MIN="3.11"
readonly FR_SITE="$DOSSIER_FR/sites-available/$NOM_SERVICE"
readonly JOURNAL_INSTALL="$DOSSIER_SCRIPT/install.log"
readonly CONF_INSTALL="$DOSSIER_SCRIPT/install.conf"
readonly CONF_INSTALL_OLD="$DOSSIER_SCRIPT/install.conf.old"
readonly AIDE="https://github.com/zobrak/wifibpub"


SIMULATION=0
TOUT_ACCEPTER=0
JOURNAL_PRET=0
RESERVES=()      # points laissés en suspens, rappelés à la fin
TEMPORAIRES=()
trap 'rm -f -- "${TEMPORAIRES[@]}"' EXIT

# --- Affichage et journal -----------------------------------------------------------

if [[ -t 2 && -z "${NO_COLOR:-}" && "${TERM:-dumb}" != "dumb" ]]; then
    readonly VERT=$'\e[32m' ROUGE=$'\e[31m' JAUNE=$'\e[33m' RAZ=$'\e[0m'
else
    readonly VERT="" ROUGE="" JAUNE="" RAZ=""
fi

# Retire tout caractère de contrôle (dont ESC) d'un texte avant affichage.
nettoie() { printf '%s' "$*" | LC_ALL=C tr -d '\000-\010\013-\037\177'; }

journaliser() {
    (( JOURNAL_PRET )) || return 0
    printf '%s %s\n' "$(date '+%F %T')" "$(nettoie "$*")" >> "$JOURNAL_INSTALL"
}

dire()      { local t; t="$(nettoie "$*")"; printf '%s\n' "$t" >&2; journaliser "$t"; }
ok()        { local t; t="$(nettoie "$*")"; printf '%s[  OK  ]%s %s\n' "$VERT" "$RAZ" "$t" >&2; journaliser "[  OK  ] $t"; }
attention() { local t; t="$(nettoie "$*")"; printf '%s[ WARN ]%s %s\n' "$JAUNE" "$RAZ" "$t" >&2; journaliser "[ WARN ] $t"; }
detail()    { local t; t="$(nettoie "$*")"; printf '  %s\n' "$t" >&2; journaliser "  $t"; }
# Liste titrée : $1 titre, puis les éléments (rien si la liste est vide).
liste()     { local titre="$1"; shift; (( $# )) || return 0; dire "$titre"; local e; for e in "$@"; do detail "$e"; done; }
echec() {
    local t; t="$(nettoie "$*")"
    printf '%s[FAILED]%s %s\n' "$ROUGE" "$RAZ" "$t" >&2
    journaliser "[FAILED] $t"
    printf 'Rien de plus n'"'"'a été fait. Détail : %s\n' "$JOURNAL_INSTALL" >&2
    exit 1
}
trap 'echec "erreur inattendue (ligne $LINENO), voir le journal"' ERR

# Vide ce qui reste dans l'entrée après une saisie refusée (texte collé sur
# plusieurs lignes), pour ne pas enchaîner les refus.
vider_entree() { local _; while IFS= read -r -t 0.2 _; do :; done; }

# Question oui/non ; $2 = O (défaut oui) ou N (défaut non).
confirmer() {
    local question defaut="${2:-O}" reponse invite
    question="$(nettoie "$1")"
    [[ "$defaut" == O ]] && invite="[O/n]" || invite="[o/N]"
    if (( TOUT_ACCEPTER )); then
        printf '%s %s oui (--oui)\n' "$question" "$invite" >&2
        journaliser "$question oui (--oui)"
        return 0
    fi
    while true; do
        printf '%s %s ' "$question" "$invite" >&2
        IFS= read -r reponse || { printf '\n' >&2; echec "entrée fermée : utiliser --oui pour un usage non interactif"; }
        [[ -z "$reponse" ]] && reponse="$defaut"
        case "${reponse,,}" in
            o|oui|y|yes) journaliser "$question oui"; return 0 ;;
            n|non|no)    journaliser "$question non"; return 1 ;;
            *) vider_entree; printf 'Répondre o ou n.\n' >&2 ;;
        esac
    done
}

aide() {
    cat >&2 <<FIN
install.sh — installation et mise à jour de $NOM_SERVICE

Usage : sudo ./install.sh [--simulation] [--oui] [--aide]

  --simulation   vérifications et plan seulement, rien n'est modifié
  --oui          répond oui aux confirmations (les saisies restent demandées)
  --aide         affiche cette aide

Paramètres : install.conf, à côté du script (secrets demandés à la saisie).
Déroulement : vérifications, plan validé par l'utilisateur, installation,
configuration, démarrage des services.
Journal détaillé : $JOURNAL_INSTALL
Aide et signalement de bugs : $AIDE
FIN
}

for argument in "$@"; do
    case "$argument" in
        --simulation) SIMULATION=1 ;;
        --oui)        TOUT_ACCEPTER=1 ;;
        --aide|-h|--help) aide; exit 0 ;;
        *) aide; echec "option inconnue : $argument" ;;
    esac
done

# --- Outils -------------------------------------------------------------------------

paquet_installe() {
    [[ "$(dpkg-query -W -f='${db:Status-Abbrev}' "$1" 2>/dev/null || true)" == ii* ]]
}

empreinte() { sha256sum "$1" | { read -r h _; printf '%s' "$h"; }; }

# Empreinte d'un fichier telle qu'enregistrée à la dernière installation.
empreinte_manifeste() {
    [[ -r "$MANIFESTE" ]] || return 0
    while read -r h chemin; do
        [[ "$chemin" == "$1" ]] && { printf '%s' "$h"; return 0; }
    done < "$MANIFESTE"
    return 0
}

# Prépare dans un fichier temporaire le contenu livré d'un fichier, avec le nom
# du service et les valeurs données (paires motif=valeur). Chemin dans PREPARE.
preparer() {
    local source="$1" contenu paire
    shift
    contenu="$(<"$source")"
    contenu="${contenu//$NOM_PROJET/$NOM_SERVICE}"
    for paire in "$@"; do
        contenu="${contenu//${paire%%=*}/${paire#*=}}"
    done
    PREPARE="$(mktemp)"
    TEMPORAIRES+=("$PREPARE")
    printf '%s\n' "$contenu" > "$PREPARE"
}

# Mots-clés obligatoires absents de la configuration installée : MANQUE.
MANQUE=()
lire_configuration() {
    MANQUE=()
    [[ -r "$CONF" ]] || return 0
    local sortie type valeur
    if ! sortie="$(python3 -I - "$DOSSIER_SCRIPT" "$NOM_PROJET" "$CONF" 2>&1 <<'PY'
import importlib, sys
sys.path.insert(0, sys.argv[1])
m = importlib.import_module(sys.argv[2])
try:
    valeurs = m.lire_valeurs(sys.argv[3])
except m.ErreurConfig as e:
    print("ERREUR", "".join(c if c.isprintable() else "?" for c in str(e)))
    sys.exit(0)
for nom in m.mots_cles_manquants(valeurs):
    print("MANQUE", nom)
PY
)"; then
        echec "lecture de la configuration impossible : $sortie"
    fi
    while read -r type valeur; do
        case "$type" in
            ERREUR) echec "configuration invalide : $valeur" ;;
            MANQUE) MANQUE+=("$valeur") ;;
        esac
    done <<< "$sortie"
    return 0
}

# Premier fichier de $1 (sites-available ou sites-enabled) qui définit le
# serveur virtuel du service, quel que soit son nom (modèle .example exclu).
site_defini() {
    local f
    for f in "$1"/*; do
        [[ -f "$f" && "$f" != *.example ]] || continue
        if grep -qE "^[[:space:]]*server[[:space:]]+${NOM_SERVICE}[[:space:]]*\{" "$f" 2>/dev/null; then
            printf '%s' "$f"
            return 0
        fi
    done
    return 0
}

# Extrait de CHANGELOG.md les sections plus récentes que la version $1
# (toutes si $1 vide, la plus récente seulement si $1 = « derniere »).
notes_de_version() {
    local depuis="$1" ligne version afficher=0 premiere=1
    while IFS= read -r ligne; do
        if [[ "$ligne" =~ ^##\ \[([0-9]+\.[0-9]+\.[0-9]+[^]]*)\] ]]; then
            version="${BASH_REMATCH[1]}"
            if [[ "$depuis" == derniere ]]; then
                (( premiere )) && afficher=1 || afficher=0
            elif [[ -z "$depuis" ]] || dpkg --compare-versions "$version" gt "$depuis"; then
                afficher=1
            else
                afficher=0
            fi
            premiere=0
        fi
        (( afficher )) && [[ -n "${ligne// /}" ]] && detail "$ligne"
    done < "$DOSSIER_SCRIPT/CHANGELOG.md"
    return 0
}

###########################################################################
# 1. Vérifications
###########################################################################

[[ "$NOM_SERVICE" =~ ^[A-Za-z_][A-Za-z0-9_-]{0,31}$ ]] \
    || echec "install.conf : ServiceName invalide : « $NOM_SERVICE » (lettres, chiffres, _ et -, 32 caractères au plus)"
(( EUID == 0 )) || echec "droits root requis : sudo ./install.sh"

if ! { touch "$JOURNAL_INSTALL" && chmod 0600 "$JOURNAL_INSTALL"; }; then
    echec "journal impossible à écrire : $JOURNAL_INSTALL"
fi
JOURNAL_PRET=1
journaliser "===== install.sh — $NOM_SERVICE — $(date '+%F %T') ====="

VERSION_LIVREE=""
if [[ -r "$DOSSIER_SCRIPT/VERSION" ]]; then
    IFS= read -r VERSION_LIVREE < "$DOSSIER_SCRIPT/VERSION" || true
fi
[[ "$VERSION_LIVREE" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]+)?$ ]] \
    || echec "fichier VERSION absent ou invalide à côté du script"
readonly VERSION_LIVREE


[[ -r /etc/os-release ]] || echec "/etc/os-release absent : système non reconnu"
# shellcheck source=/dev/null
{ IFS= read -r OS_ID; IFS= read -r OS_LIKE; IFS= read -r OS_NOM; } < <(
    . /etc/os-release
    printf '%s\n%s\n%s\n' "${ID:-}" "${ID_LIKE:-}" "${PRETTY_NAME:-inconnu}"
)
[[ "$OS_ID" == debian || " $OS_LIKE " == *" debian "* ]] \
    || echec "système non pris en charge : $OS_NOM (Debian ou dérivée attendue)"

for outil in apt-get dpkg-query dpkg getent install stat cmp mktemp sha256sum runuser; do
    command -v "$outil" >/dev/null 2>&1 || echec "outil manquant : $outil"
done
if ! { command -v systemctl >/dev/null 2>&1 && [[ -d /run/systemd/system ]]; }; then
    echec "systemd n'est pas actif : il est nécessaire pour les services"
fi

for fichier in VERSION CHANGELOG.md "$NOM_PROJET.py" "$NOM_PROJET.conf.example" "${NOM_PROJET}ctl" \
               templates/opnsense-sms.conf "systemd/$NOM_PROJET.service" \
               freeradius/module.conf freeradius/site.conf; do
    [[ -f "$DOSSIER_SCRIPT/$fichier" ]] || echec "fichier du projet manquant : $fichier"
done

# Installation existante et version.
VERSION_PRESENTE=""
if [[ -r "$VERSION_INSTALLEE" ]]; then
    IFS= read -r VERSION_PRESENTE < "$VERSION_INSTALLEE" || true
fi
DEJA_INSTALLE=0
[[ -e "$PROGRAMME" || -e "$UNITE" || -d "$DOSSIER_CONF" ]] && DEJA_INSTALLE=1

if [[ -n "$VERSION_PRESENTE" ]] && dpkg --compare-versions "$VERSION_PRESENTE" gt "$VERSION_LIVREE"; then
    echec "version installée ($VERSION_PRESENTE) plus récente que celle-ci ($VERSION_LIVREE)"
fi

# Paramètres : configuration actuelle (conf.d), configuration par défaut livrée
# et install.conf. Résultats : I_APPLIQUER (« MotClé<TAB>valeur » à écrire par
# wifipubauthctl set), I_SECRETS (à saisir), I_CLE (FingerprintKey à générer),
# I_RADIUS_ECOUTE et I_RADIUS_CLIENT (création du site FreeRADIUS).
I_APPLIQUER=() I_SECRETS=() I_MANQUE=() I_OBSOLETES=() I_RENOMMER=() I_CLE=0 I_RADIUS_ECOUTE="" I_RADIUS_CLIENT=""
I_ECOUTE="127.0.0.1" I_PORT="8642"
preparer "$DOSSIER_SCRIPT/$NOM_PROJET.conf.example"
SITE_A_CREER=0
SITE_ACTIF="$(site_defini "$DOSSIER_FR/sites-enabled")"
[[ -z "$SITE_ACTIF" && -z "$(site_defini "$DOSSIER_FR/sites-available")" ]] && SITE_A_CREER=1
if ! I_SORTIE="$(python3 -I - "$DOSSIER_SCRIPT" "$NOM_PROJET" "$PREPARE" "$CONF_INSTALL" "$SITE_A_CREER" "$DOSSIER_SURCHARGES" 2>&1 <<'PY'
import importlib, ipaddress, os, shlex, sys
sys.path.insert(0, sys.argv[1])
m = importlib.import_module(sys.argv[2])
exemple, chemin, site_a_creer, surcharges = sys.argv[3], sys.argv[4], sys.argv[5] == "1", sys.argv[6]
RADIUS = {"radiuslistenaddress": "RadiusListenAddress", "radiusclientaddress": "RadiusClientAddress"}
INSTALLATION = {"servicename"}          # lu par install.sh lui-même

def sortir(*morceaux):
    print("\t".join("".join(c if c.isprintable() else "?" for c in str(x)) for x in morceaux))

def erreur(texte):
    sortir("ERREUR", texte)
    sys.exit(0)

obsoletes = []
try:
    actuelles = m.lire_valeurs(exemple, obsoletes)
except m.ErreurConfig as e:
    erreur("configuration actuelle invalide : %s" % e)

saisies, brutes = {}, {}
if os.path.exists(chemin):
    with open(chemin, encoding="utf-8") as f:
        lignes = f.readlines()
    for numero, ligne in enumerate(lignes, 1):
        if not ligne.strip() or ligne.strip().startswith("#"):
            continue
        try:
            morceaux = shlex.split(ligne, comments=True)
        except ValueError:
            erreur("install.conf:%d : guillemets non fermés" % numero)
        if not morceaux:
            continue
        nom, mot = morceaux[0], morceaux[0].lower()
        if mot in m.ALIAS:
            erreur("install.conf:%d : %s remplacé par %s" % (numero, nom, m.NOMS[m.ALIAS[mot]]))
        if mot in m.OBSOLETES:
            erreur("install.conf:%d : %s remplacé par %s" % (numero, nom, m.OBSOLETES[mot][1]))
        if mot in m.SECRETS:
            erreur("install.conf:%d : %s est un secret, demandé à l'installation" % (numero, nom))
        if mot in INSTALLATION:
            continue
        if mot not in m.MOTS_CLES and mot not in RADIUS:
            erreur("install.conf:%d : mot-clé inconnu « %s »" % (numero, nom))
        if len(morceaux) == 1:
            continue            # à compléter : exigé seulement s'il manque
        if len(morceaux) != 2:
            erreur("install.conf:%d : %s attend une seule valeur" % (numero, nom))
        valeur = morceaux[1]
        if not valeur or any(not c.isprintable() for c in valeur) or '"' in valeur or "\\" in valeur:
            erreur("install.conf:%d : %s : valeur vide, ou avec guillemet, \\ ou caractère de contrôle" % (numero, nom))
        if mot in RADIUS:
            try:
                ipaddress.ip_address(valeur)
            except ValueError:
                erreur("install.conf:%d : %s : adresse IP attendue" % (numero, nom))
            brutes[mot] = valeur
            continue
        try:
            saisies[mot] = m.MOTS_CLES[mot][1](valeur)
        except ValueError as e:
            erreur("install.conf:%d : %s : %s" % (numero, nom, e))
        brutes[mot] = valeur

effectives = dict(actuelles)
effectives.update(saisies)
manquants = m.mots_cles_manquants(effectives)
secrets_noms = {m.NOMS[s] for s in m.SECRETS}
for nom in manquants:
    sortir("SECRET" if nom in secrets_noms else "MANQUE", nom)
if site_a_creer:
    for mot, nom in RADIUS.items():
        if mot in brutes:
            sortir("RADIUS", nom, brutes[mot])
        else:
            sortir("MANQUE", nom)
cle = effectives.get("pseudonymization", m.MOTS_CLES["pseudonymization"][2]) and not effectives.get("fingerprintkey")
if cle:
    sortir("CLE")
for mot, valeur in saisies.items():
    if actuelles.get(mot) != valeur and not (cle and mot == "pseudonymization"):
        sortir("APPLIQUER", m.NOMS[mot], brutes[mot])
# Anciens mots-clés de conf.d : renommables (ALIAS) ou à retirer (OBSOLETES).
import glob
for fichier in sorted(glob.glob(os.path.join(surcharges, "*.conf"))):
    with open(fichier, encoding="utf-8") as f:
        for ligne in f:
            try:
                morceaux = shlex.split(ligne, comments=True)
            except ValueError:
                continue
            if not morceaux:
                continue
            mot = morceaux[0].lower()
            if mot in m.ALIAS:
                sortir("RENOMMER", fichier, morceaux[0], m.NOMS[m.ALIAS[mot]])
            elif mot in m.OBSOLETES:
                sortir("OBSOLETE", morceaux[0], m.OBSOLETES[mot][1])
sortir("ECOUTE", effectives.get("listenaddress", m.MOTS_CLES["listenaddress"][2]),
       effectives.get("listenport", m.MOTS_CLES["listenport"][2]))
PY
)"; then
    echec "lecture des paramètres impossible : $I_SORTIE"
fi
while IFS=$'\t' read -r type a b c; do
    case "$type" in
        ERREUR)    echec "$a" ;;
        MANQUE)    I_MANQUE+=("$a") ;;
        SECRET)    I_SECRETS+=("$a") ;;
        CLE)       I_CLE=1 ;;
        APPLIQUER) I_APPLIQUER+=("$a"$'\t'"$b") ;;
        RADIUS)    [[ "$a" == RadiusListenAddress ]] && I_RADIUS_ECOUTE="$b" || I_RADIUS_CLIENT="$b" ;;
        ECOUTE)    I_ECOUTE="$a" I_PORT="$b" ;;
        OBSOLETE)  I_OBSOLETES+=("$a : remplacé par $b") ;;
        RENOMMER)  I_RENOMMER+=("$a"$'\t'"$b"$'\t'"$c") ;;
    esac
done <<< "$I_SORTIE"
if (( ${#I_MANQUE[@]} )); then
    [[ -e "$CONF_INSTALL" ]] || echec "install.conf absent ; paramètres obligatoires : ${I_MANQUE[*]}"
    echec "install.conf : paramètres obligatoires à compléter : ${I_MANQUE[*]}"
fi



###########################################################################
# 2. Plan
###########################################################################

P_PAQUETS=() P_AJOUT=() P_MAJ=() P_LOCAL=() P_AUTRES=() P_OBSOLETE=()

# Paquets.
MANQUANTS=()
for paquet in "${PAQUETS[@]}"; do
    paquet_installe "$paquet" || MANQUANTS+=("$paquet")
done
command -v useradd >/dev/null 2>&1 || MANQUANTS+=(passwd)
P_PAQUETS=("${MANQUANTS[@]}")

# FreeRADIUS arrêté tant qu'il n'est pas configuré pour le service.
FR_CONFIGURE=0
[[ -n "$SITE_ACTIF" ]] && FR_CONFIGURE=1
if (( ! FR_CONFIGURE )); then
    if [[ " ${MANQUANTS[*]} " == *" freeradius "* \
       || "$(systemctl is-active freeradius 2>/dev/null || true)" == active \
       || "$(systemctl is-enabled freeradius 2>/dev/null || true)" == enabled ]]; then
        P_AUTRES+=("arrêt de freeradius.service (non configuré)")
    fi
fi

# Compte et dossiers.
getent passwd "$COMPTE" >/dev/null || P_AUTRES+=("création du compte système $COMPTE")
DOSSIERS=("$DOSSIER_CONF|root|$COMPTE|0750" "$DOSSIER_SURCHARGES|root|$COMPTE|0750"
          "$DOSSIER_CODE|root|root|0755"
          "$DOSSIER_CONF/templates|root|$COMPTE|0750" "$DOSSIER_CONF/templates.d|root|$COMPTE|0750")
for entree in "${DOSSIERS[@]}"; do
    IFS='|' read -r chemin proprio groupe mode <<< "$entree"
    if [[ -L "$chemin" ]]; then
        echec "$chemin est un lien symbolique"
    elif [[ ! -e "$chemin" ]]; then
        P_AUTRES+=("création de $chemin")
    elif [[ "$(stat -c '%U:%G 0%a' "$chemin")" != "$proprio:$groupe $mode" ]]; then
        P_AUTRES+=("droits de $chemin : $proprio:$groupe $mode")
    fi
done

# Fichiers livrés : contenu préparé et état de chaque cible.
APP_ADRESSE="$I_ECOUTE"
APP_PORT="$I_PORT"
F_SOURCE=() F_CIBLE=() F_PROPRIO=() F_MODE=() F_ETAT=()
ajouter_fichier() {   # source préparée, cible, propriétaire:groupe, mode
    local etat attendu
    if [[ -L "$2" ]]; then
        echec "$2 est un lien symbolique"
    elif [[ ! -e "$2" ]]; then
        etat=nouveau
    elif cmp -s "$1" "$2"; then
        [[ "$(stat -c '%U:%G 0%a' "$2")" == "$3 $4" ]] && etat=identique || etat=droits
    else
        attendu="$(empreinte_manifeste "$2")"
        if [[ -n "$attendu" && "$attendu" == "$(empreinte "$2")" ]]; then
            etat=maj
        else
            etat=local
        fi
    fi
    F_SOURCE+=("$1") F_CIBLE+=("$2") F_PROPRIO+=("$3") F_MODE+=("$4") F_ETAT+=("$etat")
}
preparer "$DOSSIER_SCRIPT/$NOM_PROJET.py";              ajouter_fichier "$PREPARE" "$PROGRAMME" root:root 0644
for modele in "$DOSSIER_SCRIPT"/templates/*.conf; do
    preparer "$modele";                                 ajouter_fichier "$PREPARE" "$DOSSIER_CONF/templates/${modele##*/}" "root:$COMPTE" 0640
done
preparer "$DOSSIER_SCRIPT/systemd/$NOM_PROJET.service"; ajouter_fichier "$PREPARE" "$UNITE" root:root 0644
preparer "$DOSSIER_SCRIPT/${NOM_PROJET}ctl";            ajouter_fichier "$PREPARE" "$CTL" root:root 0755
preparer "$DOSSIER_SCRIPT/$NOM_PROJET.conf.example";    ajouter_fichier "$PREPARE" "$CONF" "root:$COMPTE" 0640
preparer "$DOSSIER_SCRIPT/freeradius/module.conf" "@NOM@=$NOM_SERVICE" \
    "@APP_ADRESSE@=$APP_ADRESSE" "@APP_PORT@=$APP_PORT"; ajouter_fichier "$PREPARE" "$FR_MODULE" freerad:freerad 0640
preparer "$DOSSIER_SCRIPT/freeradius/site.conf" "@NOM@=$NOM_SERVICE"
                                                        ajouter_fichier "$PREPARE" "$FR_MODELE" freerad:freerad 0640
[[ -L "$DOSSIER_FR/mods-enabled/$NOM_SERVICE" ]] || P_AUTRES+=("activation du module FreeRADIUS $NOM_SERVICE")

# Textes livrés des versions 0.8 et antérieures, remplacés par templates/.
ANCIEN_I18N=0
[[ -d "$DOSSIER_CODE/i18n" ]] && { ANCIEN_I18N=1; P_AUTRES+=("suppression de $DOSSIER_CODE/i18n (remplacé par $DOSSIER_CONF/templates)"); }

for i in "${!F_CIBLE[@]}"; do
    case "${F_ETAT[$i]}" in
        nouveau) P_AJOUT+=("${F_CIBLE[$i]}") ;;
        maj)     P_MAJ+=("${F_CIBLE[$i]}") ;;
        droits)  P_AUTRES+=("droits de ${F_CIBLE[$i]} : ${F_PROPRIO[$i]} ${F_MODE[$i]}") ;;
        local)   P_LOCAL+=("${F_CIBLE[$i]}") ;;
    esac
done

# Site FreeRADIUS : créé une seule fois. Les sites d'exemple de Debian sont
# désactivés tant que le site du service est actif ou à créer : default écoute
# sur toutes les adresses, port 1812, et empêche FreeRADIUS de démarrer.
FR_EXEMPLES=()
if (( SITE_A_CREER )); then
    P_AUTRES+=("création de $FR_SITE (secret RADIUS généré)")
    [[ -L "$DOSSIER_FR/sites-enabled/$NOM_SERVICE" ]] || P_AUTRES+=("activation du site FreeRADIUS $NOM_SERVICE")
fi
if (( SITE_A_CREER )) || [[ -n "$SITE_ACTIF" ]]; then
    for lien in sites-enabled/default sites-enabled/inner-tunnel mods-enabled/eap; do
        [[ -L "$DOSSIER_FR/$lien" ]] && FR_EXEMPLES+=("$lien")
    done
    (( ${#FR_EXEMPLES[@]} )) && P_AUTRES+=("désactivation des exemples FreeRADIUS : ${FR_EXEMPLES[*]##*/}")
fi

# Mots-clés obsolètes de la configuration personnalisée.
P_OBSOLETE=("${I_OBSOLETES[@]}")
P_RENOMMER=()
for entree in "${I_RENOMMER[@]}"; do
    IFS=$'\t' read -r fichier ancien nouveau <<< "$entree"
    P_RENOMMER+=("$ancien → $nouveau ($fichier)")
done

# Affichage du plan.
P_PARAMETRES=()
for entree in "${I_APPLIQUER[@]}"; do P_PARAMETRES+=("${entree//$'\t'/ }"); done
(( I_CLE )) && P_PARAMETRES+=("Pseudonymization on (FingerprintKey générée)")
A_FAIRE=$(( ${#P_PAQUETS[@]} + ${#P_AJOUT[@]} + ${#P_MAJ[@]} + ${#P_LOCAL[@]} + ${#P_AUTRES[@]}
            + ${#P_PARAMETRES[@]} + ${#I_SECRETS[@]} + ${#P_RENOMMER[@]} ))
[[ "$VERSION_PRESENTE" != "$VERSION_LIVREE" ]] && A_FAIRE=$(( A_FAIRE + 1 ))
if [[ -n "$VERSION_PRESENTE" && "$VERSION_PRESENTE" != "$VERSION_LIVREE" ]]; then
    dire "Mise à jour de $NOM_SERVICE $VERSION_PRESENTE vers $VERSION_LIVREE."
    dire "Notes de version :"
    notes_de_version "$VERSION_PRESENTE"
elif [[ -z "$VERSION_PRESENTE" ]] && (( DEJA_INSTALLE )); then
    dire "Mise à jour de $NOM_SERVICE (version inconnue) vers $VERSION_LIVREE."
    dire "Notes de version :"
    notes_de_version derniere
elif (( ! DEJA_INSTALLE )); then
    dire "Installation de $NOM_SERVICE $VERSION_LIVREE."
fi
liste "Paquets à installer :" "${P_PAQUETS[@]}"
liste "Fichiers à installer :" "${P_AJOUT[@]}"
liste "Fichiers à mettre à jour :" "${P_MAJ[@]}"
liste "Fichiers modifiés localement, à remplacer (copie de sauvegarde conservée) :" "${P_LOCAL[@]}"
liste "Autres opérations :" "${P_AUTRES[@]}"
liste "Paramètres à enregistrer :" "${P_PARAMETRES[@]}"
liste "Secrets à saisir :" "${I_SECRETS[@]}"
liste "Mots-clés obsolètes à renommer :" "${P_RENOMMER[@]}"
liste "Configuration à corriger :" "${P_OBSOLETE[@]}"

if (( A_FAIRE == 0 )); then
    dire "$NOM_SERVICE est déjà à jour ($VERSION_LIVREE)."
elif (( SIMULATION )); then
    dire "Simulation : aucune modification."
    exit 0
elif ! confirmer "Souhaitez-vous continuer ?"; then
    dire "Abandon."
    exit 0
fi
RENOMMER=0
if (( ${#P_RENOMMER[@]} && ! SIMULATION )) && confirmer "Renommer les mots-clés obsolètes ?"; then
    RENOMMER=1
fi

###########################################################################
# 3. Installation standard
###########################################################################

if (( A_FAIRE )); then
    if (( ${#MANQUANTS[@]} )); then
        dire "Installation des paquets : ${MANQUANTS[*]}"
        # Entrée fermée : apt et debconf ne consomment pas les réponses du script.
        apt-get update </dev/null >> "$JOURNAL_INSTALL" 2>&1 || echec "apt-get update a échoué"
        apt-get install -y "${MANQUANTS[@]}" </dev/null >> "$JOURNAL_INSTALL" 2>&1 \
            || echec "installation des paquets échouée"
        for paquet in "${MANQUANTS[@]}"; do
            paquet_installe "$paquet" || echec "$paquet absent après apt-get"
        done
        ok "Installation des paquets"
    fi

    # Versions (contrat du module rest vérifié sur FreeRADIUS 3.2.7).
    V_FR="$(dpkg-query -W -f='${Version}' freeradius)"
    V_AMONT="${V_FR#*:}"; V_AMONT="${V_AMONT%%[+~-]*}"
    dpkg --compare-versions "$V_AMONT" ge "$FREERADIUS_MIN" \
        || echec "FreeRADIUS $V_FR trop ancien ($FREERADIUS_MIN au minimum)"
    V_PY="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
    dpkg --compare-versions "$V_PY" ge "$PYTHON_MIN" || echec "Python $V_PY trop ancien ($PYTHON_MIN au minimum)"

    # FreeRADIUS au repos tant qu'il n'est pas configuré.
    if (( ! FR_CONFIGURE )) && [[ "$(systemctl is-active freeradius 2>/dev/null || true)" == active \
         || "$(systemctl is-enabled freeradius 2>/dev/null || true)" == enabled ]]; then
        systemctl disable --now freeradius >> "$JOURNAL_INSTALL" 2>&1 \
            || echec "arrêt de FreeRADIUS impossible"
        ok "Arrêt de freeradius.service"
    fi


    if ! getent passwd "$COMPTE" >/dev/null; then
        useradd --system --user-group --no-create-home --home-dir /nonexistent \
            --shell /usr/sbin/nologin "$COMPTE"
        ok "Création du compte système $COMPTE"
    fi

    for entree in "${DOSSIERS[@]}"; do
        IFS='|' read -r chemin proprio groupe mode <<< "$entree"
        if [[ ! -e "$chemin" ]]; then
            install -d -o "$proprio" -g "$groupe" -m "$mode" "$chemin"
            ok "Création de $chemin"
        elif [[ "$(stat -c '%U:%G 0%a' "$chemin")" != "$proprio:$groupe $mode" ]]; then
            chown "$proprio:$groupe" "$chemin"; chmod "$mode" "$chemin"
            ok "Droits de $chemin"
        fi
    done

    UNITE_MODIFIEE=0 APP_MODIFIEE=0 FR_MODULE_MODIFIE=0
    for i in "${!F_CIBLE[@]}"; do
        cible="${F_CIBLE[$i]}"
        [[ "${F_ETAT[$i]}" == identique ]] && continue
        sauvegarde=""
        if [[ "${F_ETAT[$i]}" == local ]]; then
            sauvegarde="$cible.sauvegarde-$(date +%Y%m%d-%H%M%S)"
            cp -p -- "$cible" "$sauvegarde"
        fi
        install -o "${F_PROPRIO[$i]%%:*}" -g "${F_PROPRIO[$i]#*:}" -m "${F_MODE[$i]}" "${F_SOURCE[$i]}" "$cible"
        case "${F_ETAT[$i]}" in
            nouveau) ok "Installation de $cible" ;;
            droits)  ok "Droits de $cible" ;;
            local)   ok "Remplacement de $cible (sauvegarde : $sauvegarde)" ;;
            *)       ok "Mise à jour de $cible" ;;
        esac
        [[ "$cible" == "$UNITE" ]] && UNITE_MODIFIEE=1
        [[ "$cible" == "$FR_MODULE" ]] && FR_MODULE_MODIFIE=1
        APP_MODIFIEE=1
    done

    if (( ANCIEN_I18N )); then
        rm -rf -- "$DOSSIER_CODE/i18n"
        ok "Suppression de $DOSSIER_CODE/i18n"
        APP_MODIFIEE=1
    fi

    # Version installée et empreintes (détection des modifications locales).
    install -o root -g root -m 0644 "$DOSSIER_SCRIPT/VERSION" "$VERSION_INSTALLEE"
    temporaire="$(mktemp)"; TEMPORAIRES+=("$temporaire")
    for cible in "${F_CIBLE[@]}"; do
        printf '%s  %s\n' "$(empreinte "$cible")" "$cible"
    done > "$temporaire"
    install -o root -g root -m 0644 "$temporaire" "$MANIFESTE"

    if [[ ! -L "$DOSSIER_FR/mods-enabled/$NOM_SERVICE" ]]; then
        [[ -e "$DOSSIER_FR/mods-enabled/$NOM_SERVICE" ]] \
            && echec "$DOSSIER_FR/mods-enabled/$NOM_SERVICE existe et n'est pas un lien : à vérifier à la main"
        ln -s "../mods-available/$NOM_SERVICE" "$DOSSIER_FR/mods-enabled/$NOM_SERVICE"
        ok "Activation du module FreeRADIUS $NOM_SERVICE"
        FR_MODULE_MODIFIE=1
    fi

    if (( UNITE_MODIFIEE )); then
        systemctl daemon-reload || echec "systemctl daemon-reload a échoué"
    fi

    # Mots-clés obsolètes de conf.d : seul le nom change ; copie de sauvegarde.
    if (( RENOMMER )); then
        declare -A FICHIERS_RENOMMES=()
        for entree in "${I_RENOMMER[@]}"; do
            IFS=$'\t' read -r fichier _ _ <<< "$entree"
            FICHIERS_RENOMMES["$fichier"]=1
        done
        for fichier in "${!FICHIERS_RENOMMES[@]}"; do
            sauvegarde="$fichier.sauvegarde-$(date +%Y%m%d-%H%M%S)"
            cp -p -- "$fichier" "$sauvegarde"
            python3 -I - "$DOSSIER_SCRIPT" "$NOM_PROJET" "$fichier" <<'PY' || echec "renommage impossible : $fichier"
import importlib, os, re, sys, tempfile
sys.path.insert(0, sys.argv[1])
m = importlib.import_module(sys.argv[2])
fichier = sys.argv[3]
with open(fichier, encoding="utf-8") as f:
    lignes = f.readlines()
for i, ligne in enumerate(lignes):
    r = re.match(r"(\s*)(\S+)(.*)", ligne, re.S)
    if r and r.group(2).lower() in m.ALIAS:
        lignes[i] = r.group(1) + m.NOMS[m.ALIAS[r.group(2).lower()]] + r.group(3)
etat = os.stat(fichier)
d, t = tempfile.mkstemp(dir=os.path.dirname(fichier), prefix=".wpa-")
with os.fdopen(d, "w", encoding="utf-8") as f:
    f.writelines(lignes)
os.chown(t, etat.st_uid, etat.st_gid)
os.chmod(t, etat.st_mode & 0o777)
os.replace(t, fichier)
PY
            ok "Mots-clés renommés dans $fichier (sauvegarde : $sauvegarde)"
        done
        APP_MODIFIEE=1
    fi

    # Paramètres d'install.conf, puis secrets (saisie interactive).
    for entree in "${I_APPLIQUER[@]}"; do
        IFS=$'\t' read -r cle valeur <<< "$entree"
        SORTIE_CTL="$("$CTL" set "$cle" "$valeur" 2>&1)" || echec "$cle : $SORTIE_CTL"
        ok "Paramètre $cle $valeur"
        APP_MODIFIEE=1
    done
    if (( I_CLE )); then
        SORTIE_CTL="$("$CTL" set Pseudonymization on --genkey 2>&1)" || echec "Pseudonymization : $SORTIE_CTL"
        ok "Paramètre Pseudonymization on (FingerprintKey générée)"
        APP_MODIFIEE=1
    fi
    for cle in "${I_SECRETS[@]}"; do
        "$CTL" set "$cle" > /dev/null || echec "$cle : saisie non enregistrée"
        ok "Secret $cle"
        APP_MODIFIEE=1
    done

    # Site FreeRADIUS, créé une seule fois.
    if (( SITE_A_CREER )); then
        SECRET_RADIUS="$(od -An -tx1 -N16 /dev/urandom | tr -d ' \n')"
        [[ "$SECRET_RADIUS" =~ ^[0-9a-f]{32}$ ]] || echec "génération du secret RADIUS impossible"
        preparer "$DOSSIER_SCRIPT/freeradius/site.conf" "@NOM@=$NOM_SERVICE" \
            "ADRESSE_ECOUTE=$I_RADIUS_ECOUTE" "ADRESSE_PORTAIL=$I_RADIUS_CLIENT" "SECRET_RADIUS=$SECRET_RADIUS"
        unset SECRET_RADIUS
        install -o freerad -g freerad -m 0640 "$PREPARE" "$FR_SITE"
        rm -f -- "$PREPARE"
        ok "Création de $FR_SITE"
        if [[ ! -L "$DOSSIER_FR/sites-enabled/$NOM_SERVICE" ]]; then
            [[ -e "$DOSSIER_FR/sites-enabled/$NOM_SERVICE" ]] \
                && echec "$DOSSIER_FR/sites-enabled/$NOM_SERVICE existe et n'est pas un lien : à vérifier à la main"
            ln -s "../sites-available/$NOM_SERVICE" "$DOSSIER_FR/sites-enabled/$NOM_SERVICE"
            ok "Activation du site FreeRADIUS $NOM_SERVICE"
        fi
        FR_MODULE_MODIFIE=1
    fi
    for lien in "${FR_EXEMPLES[@]}"; do
        rm -f -- "$DOSSIER_FR/$lien"
        ok "Désactivation de $lien"
        FR_MODULE_MODIFIE=1
    done
else
    UNITE_MODIFIEE=0 APP_MODIFIEE=0 FR_MODULE_MODIFIE=0
fi

###########################################################################
# 4. État de la configuration
###########################################################################

lire_configuration
CONFIG_VALIDE=0
if (( ${#MANQUE[@]} == 0 )); then
    if VERIF="$(runuser -u "$COMPTE" -- /usr/bin/python3 -I "$PROGRAMME" --config "$CONF" --verifier 2>&1)"; then
        CONFIG_VALIDE=1
    else
        echec "configuration du service invalide : $VERIF"
    fi
fi
FR_VALIDE=0
if [[ -n "$(site_defini "$DOSSIER_FR/sites-enabled")" ]]; then
    if VERIF="$(freeradius -XC 2>&1)"; then
        FR_VALIDE=1
    else
        printf '%s\n' "$VERIF" | tail -n 8 | while IFS= read -r ligne; do detail "$ligne"; done
        echec "configuration de FreeRADIUS invalide (freeradius -XC) : FreeRADIUS reste arrêté"
    fi
fi
FR_MODIFIE=$FR_MODULE_MODIFIE

# FreeRADIUS en service avec une configuration antérieure à la dernière
# modification (site ou module activé, modifié ou retiré) : à redémarrer.
if (( FR_VALIDE )) && [[ "$(systemctl is-active freeradius 2>/dev/null || true)" == active ]]; then
    FR_PID="$(systemctl show -p MainPID --value freeradius 2>/dev/null || true)"
    FR_DEBUT="$(python3 -I -c '
import os, sys
with open("/proc/%d/stat" % int(sys.argv[1])) as f:
    debut = int(f.read().rsplit(")", 1)[1].split()[19])
with open("/proc/stat") as f:
    amorcage = next(int(l.split()[1]) for l in f if l.startswith("btime "))
print(int(amorcage + debut / os.sysconf("SC_CLK_TCK")))' "${FR_PID:-0}" 2>/dev/null || true)"
    FR_CONF="$( { find "$DOSSIER_FR/sites-enabled" "$DOSSIER_FR/mods-enabled" -maxdepth 1 -printf '%T@\n'
                  find -L "$DOSSIER_FR/sites-enabled" "$DOSSIER_FR/mods-enabled" -maxdepth 1 -printf '%T@\n'
                  stat -c '%Y' "$DOSSIER_FR/radiusd.conf" "$DOSSIER_FR/clients.conf"
                } 2>/dev/null | cut -d. -f1 | sort -n | tail -n 1 || true)"
    if [[ "$FR_DEBUT" =~ ^[0-9]+$ && "$FR_CONF" =~ ^[0-9]+$ ]] && (( FR_DEBUT < FR_CONF )); then
        FR_MODIFIE=1
    fi
fi

###########################################################################
# 5. Services
###########################################################################

# $1 service, $2 configuration valide (1/0), $3 modifié (1/0)
service() {
    local nom="$1" valide="$2" modifie="$3" actif active
    actif="$(systemctl is-active "$nom" 2>/dev/null || true)"
    active="$(systemctl is-enabled "$nom" 2>/dev/null || true)"
    (( valide )) || return 0
    if [[ "$actif" != active || "$active" != enabled ]]; then
        systemctl enable --now "$nom" >> "$JOURNAL_INSTALL" 2>&1 || true
    elif (( modifie )); then
        systemctl restart "$nom" >> "$JOURNAL_INSTALL" 2>&1 || true
    else
        return 0
    fi
    sleep 2
    [[ "$(systemctl is-active "$nom" 2>/dev/null || true)" == active ]] \
        || echec "échec du démarrage de $nom.service : journalctl -u $nom -n 50"
    if [[ "$actif" == active ]]; then ok "Redémarrage de $nom.service"; else ok "Démarrage de $nom.service"; fi
}
service "$NOM_SERVICE" "$CONFIG_VALIDE" "$APP_MODIFIEE"
service freeradius "$FR_VALIDE" "$FR_MODIFIE"

###########################################################################
# Bilan et configuration
###########################################################################

CONFIG_REQUISE=()
if (( ! CONFIG_VALIDE )); then
    CONFIG_REQUISE+=("$NOM_SERVICE : ${MANQUE[*]} (${NOM_SERVICE}ctl set PARAMÈTRE VALEUR, voir $CONF)")
fi
(( FR_VALIDE )) || CONFIG_REQUISE+=("freeradius : serveur virtuel $NOM_SERVICE absent de $DOSSIER_FR/sites-enabled")
liste "Configuration requise :" "${CONFIG_REQUISE[@]}"
for reserve in "${RESERVES[@]}"; do attention "$reserve"; done

# install.conf : renommé install.conf.old, ou fusionné dans celui-ci.
if [[ -e "$CONF_INSTALL" ]]; then
    if [[ ! -e "$CONF_INSTALL_OLD" ]]; then
        mv -- "$CONF_INSTALL" "$CONF_INSTALL_OLD"
        ok "install.conf renommé install.conf.old"
    else
        python3 -I - "$CONF_INSTALL" "$CONF_INSTALL_OLD" <<'PY' || echec "fusion de install.conf dans install.conf.old impossible"
import os, shlex, sys, tempfile
neuf, ancien = sys.argv[1], sys.argv[2]

def mot(ligne):
    try:
        morceaux = shlex.split(ligne, comments=True)
    except ValueError:
        return None, 0
    return (morceaux[0].lower(), len(morceaux)) if morceaux else (None, 0)

with open(ancien, encoding="utf-8") as f:
    lignes = f.readlines()
if lignes and not lignes[-1].endswith("\n"):
    lignes[-1] += "\n"
with open(neuf, encoding="utf-8") as f:
    for ligne in f:
        cle, n = mot(ligne)
        if cle is None or n < 2:
            continue
        for i, existante in enumerate(lignes):
            if mot(existante)[0] == cle:
                lignes[i] = ligne if ligne.endswith("\n") else ligne + "\n"
                break
        else:
            lignes.append(ligne if ligne.endswith("\n") else ligne + "\n")
d, t = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(ancien)))
with os.fdopen(d, "w", encoding="utf-8") as f:
    f.writelines(lignes)
os.chmod(t, os.stat(ancien).st_mode & 0o777)
os.replace(t, ancien)
os.unlink(neuf)
PY
        ok "install.conf fusionné dans install.conf.old"
    fi
fi
(( SITE_A_CREER )) && [[ -e "$FR_SITE" ]] && dire "Secret RADIUS à reporter dans le portail captif : $FR_SITE"

# Tout est en place : état affiché par wifipubauthctl (affichage seulement, pas
# de copie dans le journal d'installation).
if (( CONFIG_VALIDE && FR_VALIDE )); then
    for commande in check status help; do
        printf '\n$ %s %s\n' "${CTL##*/}" "$commande" >&2
        { "$CTL" "$commande" 2>&1 || true; } | LC_ALL=C tr -d '\000-\010\013-\037\177' >&2
    done
    printf '\n' >&2
fi
dire "Messages : $DOSSIER_CONF/templates/, à personnaliser dans $DOSSIER_CONF/templates.d/"
dire "Journal : $JOURNAL_INSTALL"
