# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests sans réseau extérieur : faux fournisseur de vouchers (API OPNsense) et faux OVH sur 127.0.0.1."""

import http.server
import json
import os
import re
import sys
import threading
import time
import urllib.parse
import urllib.request

import pytest

ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(ICI))
import wifipubauth as w  # noqa: E402

RACINE = os.path.dirname(ICI)


class Faux(http.server.BaseHTTPRequestHandler):
    recus = []
    reponse = None

    def do_POST(self):
        corps = self.rfile.read(int(self.headers["Content-Length"]))
        type(self).recus.append((self.path, dict(self.headers), corps))
        code, contenu = type(self).reponse
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(contenu)))
        self.end_headers()
        self.wfile.write(contenu)

    def log_message(self, *a):
        pass


def lancer(classe):
    serveur = http.server.ThreadingHTTPServer(("127.0.0.1", 0), classe)
    threading.Thread(target=serveur.serve_forever, daemon=True).start()
    return serveur


@pytest.fixture
def faux():
    class Opn(Faux):
        recus = []
        reponse = (200, json.dumps([{"username": "Ab3k", "password": "x7Pq9", "vouchergroup": "sms",
                                     "validity": 43200, "expirytime": 3600, "starttime": None}]).encode())

    class Ovh(Faux):
        recus = []
        reponse = (200, b'{"status":100,"creditLeft":"9","SmsIds":["123"]}')

    so, ss = lancer(Opn), lancer(Ovh)
    yield Opn, Ovh, so.server_address[1], ss.server_address[1]
    so.shutdown()
    ss.shutdown()


def ecrire_config(tmp_path, port_opn, port_sms, extra=""):
    conf_d = tmp_path / "conf.d"
    conf_d.mkdir()
    (conf_d / "secrets.conf").write_text(
        extra + "Pseudonymization on\nFingerprintKey cle-test\nOpnsenseApiKey k\nOpnsenseApiSecret s\n"
        "SmsApiProvider ovh\nOvhSmsPassword Abcd1234\n"
        "OpnsenseUrl http://127.0.0.1:%d\nOpnsenseVoucherServer Vouchers\nVoucherApiProvider opnsense\nOvhSmsAccount sms-test-1\n"
        "OvhSmsLogin utilisateur\nOvhSmsSender Expediteur\nOvhSmsUrl http://127.0.0.1:%d/sms\n" % (port_opn, port_sms))
    exemple = open(os.path.join(RACINE, "wifipubauth.conf.example")).read()
    exemple = exemple.replace("/etc/wifipubauth/conf.d", str(conf_d))
    exemple = exemple.replace("/usr/local/lib/wifipubauth/i18n", os.path.join(RACINE, "i18n"))
    chemin = tmp_path / "w.conf"
    chemin.write_text(exemple)
    return str(chemin)


def attributs(nom, mdp="demande", mac="aa-bb-cc-dd-ee-ff"):
    a = {"User-Name": {"type": "string", "value": [nom]},
         "User-Password": {"type": "string", "value": [mdp]},
         "NAS-Identifier": {"type": "string", "value": ["opnsense"]}}
    if mac:
        a["Calling-Station-Id"] = {"type": "string", "value": [mac]}
    return a


def service(chemin):
    config = w.lire_config(chemin)
    s = w.Service(config, w.lire_catalogue(config))
    threading.Thread(target=s.travailleur, daemon=True).start()
    return s


# --- Configuration ------------------------------------------------------------------

def test_exemple_et_premiere_valeur_retenue(tmp_path, faux):
    chemin = ecrire_config(tmp_path, 1, 2, extra="LimitPerIdentifier 5/1h\n")
    c = w.lire_config(chemin)
    assert c.limite_identifiant == (5, 3600)      # surcharge conf.d prioritaire
    assert c.voucher_validite == 43200 and c.voucher_expiration == 3600
    assert c.canaux == ("sms",) and c.port == 8642


def test_mot_cle_inconnu(tmp_path):
    p = tmp_path / "c.conf"
    p.write_text("Inconnu 1\n")
    with pytest.raises(w.ErreurConfig, match="c.conf:1"):
        w.lire_config(str(p))


def test_obligatoires_absents(tmp_path):
    p = tmp_path / "c.conf"
    p.write_text("ListenPort 9000\n")
    with pytest.raises(w.ErreurConfig, match="obligatoires"):
        w.lire_config(str(p))


@pytest.mark.parametrize("ligne", ["VoucherValidity 12", "LimitGlobal 0/1d", "ListenPort 70000",
                                   "Channels sms,email", "OpnsenseUrl ftp://x"])
def test_valeurs_invalides(tmp_path, ligne):
    p = tmp_path / "c.conf"
    p.write_text(ligne + "\n")
    with pytest.raises(w.ErreurConfig):
        w.lire_config(str(p))


# --- Validation ---------------------------------------------------------------------

@pytest.mark.parametrize("entree,attendu", [
    ("0612345678", ("sms", "0033612345678")),
    ("07 12 34 56 78", ("sms", "0033712345678")),
    ("07.12.34.56.78", ("sms", "0033712345678")),
    ("Nom.Prenom@Exemple.FR", ("email", "Nom.Prenom@exemple.fr")),
])
def test_identifiants_acceptes(entree, attendu):
    assert w.identifiant_valide(entree) == attendu


@pytest.mark.parametrize("entree", ["0112345678", "061234567", "+33612345678", "06123456789",
                                    "0612345678\n", "a@b", "a b@c.fr", "", None, "x" * 300,
                                    "06\x1b[31m12345678"])
def test_identifiants_refuses(entree):
    assert w.identifiant_valide(entree) is None


def test_mac():
    assert w.mac_normalisee("AA-BB-CC-DD-EE-FF") == "aa:bb:cc:dd:ee:ff"
    assert w.mac_normalisee("aa:bb") is None


# --- Parcours complet ---------------------------------------------------------------

def attendre(cond, delai=3):
    fin = time.time() + delai
    while time.time() < fin:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_parcours_sms(tmp_path, faux, capsys):
    Opn, Ovh, po, ps = faux
    s = service(ecrire_config(tmp_path, po, ps))
    s.demande(attributs("06 12 34 56 78"))
    assert attendre(lambda: Ovh.recus)
    chemin, entetes, corps = Opn.recus[0]
    assert chemin == "/api/captiveportal/voucher/generateVouchers/Vouchers"
    assert entetes["Authorization"] == "Basic azpz"
    assert json.loads(corps) == {"count": 1, "validity": 43200, "expirytime": 3600, "vouchergroup": "sms"}
    champs = urllib.parse.parse_qs(Ovh.recus[0][2].decode())
    assert champs["to"] == ["0033612345678"] and champs["from"] == ["Expediteur"]
    assert champs["login"] == ["utilisateur"] and champs["noStop"] == ["1"]
    assert "Ab3k" in champs["message"][0] and "x7Pq9" in champs["message"][0]
    assert "12 h" in champs["message"][0]
    sortie = capsys.readouterr().err
    assert '<6>sms_sent voucher envoyé par SMS' in sortie
    assert "0612345678" not in sortie and "0033612345678" not in sortie and "Abcd1234" not in sortie


def test_marqueur_absent_rien_envoye(tmp_path, faux):
    Opn, Ovh, po, ps = faux
    s = service(ecrire_config(tmp_path, po, ps))
    s.demande(attributs("0612345678", mdp="Ab3k"))
    s.demande(attributs("0612345678", mdp="DEMANDE"))
    time.sleep(0.2)
    assert not Opn.recus


def test_email_refuse_canal_absent(tmp_path, faux, capsys):
    Opn, Ovh, po, ps = faux
    s = service(ecrire_config(tmp_path, po, ps))
    s.demande(attributs("a@b.fr"))
    time.sleep(0.2)
    assert not Opn.recus and "channel_unavailable" in capsys.readouterr().err


def test_limites(tmp_path, faux, capsys):
    Opn, Ovh, po, ps = faux
    s = service(ecrire_config(tmp_path, po, ps))
    for _ in range(3):
        s.demande(attributs("0612345678", mac="aa-bb-cc-dd-ee-01"))
    assert attendre(lambda: len(Ovh.recus) == 2)
    time.sleep(0.2)
    assert len(Ovh.recus) == 2          # 2 par numéro
    s.demande(attributs("0700000001", mac="aa-bb-cc-dd-ee-01"))
    s.demande(attributs("0700000002", mac="aa-bb-cc-dd-ee-01"))
    assert attendre(lambda: len(Ovh.recus) == 3)
    time.sleep(0.2)
    assert len(Ovh.recus) == 3          # 3 par MAC
    sortie = capsys.readouterr().err
    assert 'rule=identifier' in sortie and 'rule=mac' in sortie


def test_limite_globale(tmp_path, faux):
    Opn, Ovh, po, ps = faux
    s = service(ecrire_config(tmp_path, po, ps, extra="LimitGlobal 2/1d\n"))
    for i in range(4):
        s.demande(attributs("06000000%02d" % i, mac=None))
    assert attendre(lambda: len(Ovh.recus) == 2)
    time.sleep(0.2)
    assert len(Ovh.recus) == 2


def test_echec_opnsense_pas_de_sms(tmp_path, faux, capsys):
    Opn, Ovh, po, ps = faux
    Opn.reponse = (200, b'{"status":"error"}')
    s = service(ecrire_config(tmp_path, po, ps))
    s.demande(attributs("0612345678"))
    assert attendre(lambda: "voucher_failed" in capsys.readouterr().err)
    assert not Ovh.recus


def test_echec_ovh(tmp_path, faux, capsys):
    Opn, Ovh, po, ps = faux
    Ovh.reponse = (200, b'{"status":401,"message":"IP not allowed"}')
    s = service(ecrire_config(tmp_path, po, ps))
    s.demande(attributs("0612345678"))
    assert attendre(lambda: "sms_status_401" in capsys.readouterr().err)


def test_voucher_suspect_refuse(tmp_path, faux, capsys):
    Opn, Ovh, po, ps = faux
    Opn.reponse = (200, json.dumps([{"username": "a\x1b[2J", "password": "b"}]).encode())
    s = service(ecrire_config(tmp_path, po, ps))
    s.demande(attributs("0612345678"))
    assert attendre(lambda: "opnsense_bad_voucher" in capsys.readouterr().err)
    assert not Ovh.recus


# --- Serveur HTTP ---------------------------------------------------------------------

def test_serveur_repond_toujours_401(tmp_path, faux):
    Opn, Ovh, po, ps = faux
    s = service(ecrire_config(tmp_path, po, ps))
    serveur = http.server.ThreadingHTTPServer(("127.0.0.1", 0), w.fabrique_gestionnaire(s))
    threading.Thread(target=serveur.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % serveur.server_address[1]

    def poster(chemin, corps):
        r = urllib.request.Request(base + chemin, data=corps, method="POST",
                                   headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(r, timeout=3)
        except urllib.error.HTTPError as e:
            return e.code
        return 200

    assert poster("/demande", json.dumps(attributs("0612345678")).encode()) == 401
    assert poster("/demande", b"pas du json") == 401
    assert poster("/demande", b"[1]") == 401
    assert poster("/autre", b"{}") == 404
    assert attendre(lambda: Ovh.recus)
    serveur.shutdown()


# --- Journal ------------------------------------------------------------------------

def test_journal_natif(tmp_path, monkeypatch):
    import socket
    chemin = str(tmp_path / "journal.sock")
    recepteur = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    recepteur.bind(chemin)
    monkeypatch.setattr(w, "SOCKET_JOURNAL", chemin)
    monkeypatch.setattr(w, "vers_journald", lambda: True)
    j = w.Journal()
    j.ecrire("sms_sent", "info", {"hid": "abc", "voucher": "Ab\x1b[2J3k", "sms_id": 7})
    brut = recepteur.recv(65536)
    champs = dict(ligne.split("=", 1) for ligne in brut.decode().splitlines())
    assert champs["PRIORITY"] == "6"
    assert champs["SYSLOG_IDENTIFIER"] == "wifipubauth"
    assert champs["MESSAGE_ID"] == "b3f0a50c9ec24dccb556c5334faf13aa"
    assert champs["WPA_EVENT"] == "sms_sent"
    assert champs["WPA_VOUCHER"] == "Ab[2J3k"          # caractère de contrôle retiré
    assert champs["WPA_SMS_ID"] == "7"
    assert champs["MESSAGE"].startswith("voucher envoyé par SMS (")
    for cle in champs:
        assert re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", cle)


def test_journal_niveaux(tmp_path, monkeypatch):
    import socket
    chemin = str(tmp_path / "journal.sock")
    recepteur = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    recepteur.bind(chemin)
    monkeypatch.setattr(w, "SOCKET_JOURNAL", chemin)
    monkeypatch.setattr(w, "vers_journald", lambda: True)
    j = w.Journal()
    j.ecrire("sms_failed", "error", {})
    j.ecrire("queue_full", "warning", {})
    assert b"PRIORITY=3\n" in recepteur.recv(65536)
    assert b"PRIORITY=4\n" in recepteur.recv(65536)


def test_serialisation_saut_de_ligne():
    brut = w.Journal.serialiser({"A": "x\ny"})
    assert brut == b"A\n" + (3).to_bytes(8, "little") + b"x\ny\n"


def test_repli_sortie_erreur(capsys, monkeypatch):
    monkeypatch.setattr(w, "vers_journald", lambda: False)
    j = w.Journal()
    j.ecrire("voucher_failed", "error", {"detail": "http_500"})
    assert capsys.readouterr().err == "<3>voucher_failed échec de la création du voucher (detail=http_500)\n"


def test_identifiant_en_clair_par_defaut(tmp_path, faux, capsys):
    Opn, Ovh, po, ps = faux
    s = service(ecrire_config(tmp_path, po, ps, extra="Pseudonymization off\n"))
    s.demande(attributs("06 12 34 56 78"))
    assert attendre(lambda: Ovh.recus)
    sortie = capsys.readouterr().err
    assert "identifier=0033612345678" in sortie and "hid=" not in sortie


def test_pseudonymisation_sans_cle_refusee(tmp_path, faux):
    Opn, Ovh, po, ps = faux
    chemin = ecrire_config(tmp_path, po, ps)
    f = tmp_path / "conf.d" / "secrets.conf"
    f.write_text(f.read_text().replace("FingerprintKey cle-test\n", ""))
    with pytest.raises(w.ErreurConfig, match="FingerprintKey"):
        w.lire_config(chemin)


def test_anciens_mots_cles(tmp_path, faux):
    Opn, Ovh, po, ps = faux
    chemin = ecrire_config(tmp_path, po, ps)
    f = tmp_path / "conf.d" / "secrets.conf"
    t = f.read_text().replace("SmsApiProvider ovh\n", "")
    for nouveau, ancien in (("OvhSmsAccount", "SmsAccount"), ("OvhSmsLogin", "SmsLogin"),
                            ("OvhSmsSender", "SmsSender"), ("OvhSmsUrl", "SmsUrl"),
                            ("OvhSmsPassword", "SmsPassword")):
        t = t.replace(nouveau + " ", ancien + " ")
    f.write_text(t)
    c = w.lire_config(chemin)
    assert c.sms_fournisseur == "ovh" and c.ovh_sms_compte == "sms-test-1"
    assert ("OvhSmsAccount", "SmsAccount") in c.obsoletes


def test_fournisseurs_obligatoires_sans_parametres(tmp_path):
    (tmp_path / "conf.d").mkdir()
    exemple = open(os.path.join(RACINE, "wifipubauth.conf.example")).read()
    chemin = tmp_path / "w.conf"
    chemin.write_text(exemple.replace("/etc/wifipubauth/conf.d", str(tmp_path / "conf.d")))
    manquants = w.mots_cles_manquants(w.lire_valeurs(str(chemin)))
    assert "VoucherApiProvider" in manquants and "SmsApiProvider" in manquants


def test_fournisseur_voucher_inconnu(tmp_path, faux):
    Opn, Ovh, po, ps = faux
    chemin = ecrire_config(tmp_path, po, ps, extra="VoucherApiProvider autre\n")
    with pytest.raises(w.ErreurConfig, match="fournisseur inconnu"):
        w.lire_config(chemin)


def test_voucherprovider_ancien_nom(tmp_path, faux):
    Opn, Ovh, po, ps = faux
    chemin = ecrire_config(tmp_path, po, ps)
    f = tmp_path / "conf.d" / "secrets.conf"
    f.write_text(f.read_text().replace("VoucherApiProvider opnsense\n", "")
                 .replace("OpnsenseVoucherServer ", "VoucherProvider "))
    c = w.lire_config(chemin)
    assert c.voucher_fournisseur == "opnsense" and c.voucher_serveur == "Vouchers"
    assert c.obsoletes == [("OpnsenseVoucherServer", "VoucherProvider")]
    assert c.sms_fournisseur == "ovh"


def test_fournisseurs_deduits_des_parametres(tmp_path, faux):
    Opn, Ovh, po, ps = faux
    chemin = ecrire_config(tmp_path, po, ps)
    f = tmp_path / "conf.d" / "secrets.conf"
    f.write_text(f.read_text().replace("VoucherApiProvider opnsense\n", "").replace("SmsApiProvider ovh\n", ""))
    c = w.lire_config(chemin)
    assert c.voucher_fournisseur == "opnsense" and c.sms_fournisseur == "ovh" and c.obsoletes == []


# --- wifipubauthctl set ---------------------------------------------------------------

import importlib.machinery  # noqa: E402
import importlib.util  # noqa: E402
import grp as _grp  # noqa: E402


@pytest.fixture
def ctl(tmp_path, monkeypatch):
    chargeur = importlib.machinery.SourceFileLoader("wpactl", os.path.join(RACINE, "wifipubauthctl"))
    spec = importlib.util.spec_from_loader("wpactl", chargeur)
    c = importlib.util.module_from_spec(spec)
    chargeur.exec_module(c)
    (tmp_path / "conf.d").mkdir()
    principal = tmp_path / "w.conf"
    principal.write_text("Include %s/conf.d/*.conf\n" % tmp_path)
    monkeypatch.setattr(c, "CONF", str(principal))
    monkeypatch.setattr(c, "DOSSIER_SURCHARGES", str(tmp_path / "conf.d"))
    monkeypatch.setattr(c, "CONF_LOCALE", str(tmp_path / "conf.d" / "local.conf"))
    monkeypatch.setattr(c, "exiger_root", lambda: None)
    monkeypatch.setattr(c, "application", lambda: w)
    monkeypatch.setattr(c.grp, "getgrnam", lambda nom: _grp.getgrgid(os.getgid()))
    return c


def executer(c, monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["wifipubauthctl", *argv])
    try:
        return c.main()
    except SystemExit as e:
        return e.code


def test_set_valeur(ctl, monkeypatch, tmp_path):
    assert executer(ctl, monkeypatch, "set", "opnsenseurl", "https://exemple.org") == 0
    assert executer(ctl, monkeypatch, "set", "OpnsenseUrl", "https://autre.org") == 0
    texte = (tmp_path / "conf.d" / "local.conf").read_text()
    assert texte == 'OpnsenseUrl "https://autre.org"\n'
    assert oct(os.stat(tmp_path / "conf.d" / "local.conf").st_mode & 0o777) == "0o640"


@pytest.mark.parametrize("argv", [("set", "Inconnu", "x"), ("set", "OpnsenseUrl", "ftp://x"),
                                  ("set", "SmsAccount", "x"), ("set", "SmsApiProvider", "free"),
                                  ("set", "OpnsenseApiSecret", "en-clair"),
                                  ("set", "Pseudonymization", "peut-etre"),
                                  ("set", "OpnsenseUrl", 'https://a"b')])
def test_set_refus(ctl, monkeypatch, tmp_path, argv):
    assert executer(ctl, monkeypatch, *argv) == 2
    assert not (tmp_path / "conf.d" / "local.conf").exists()


def test_set_secret_sans_terminal(ctl, monkeypatch):
    monkeypatch.setattr(sys, "stdin", open(os.devnull))
    assert executer(ctl, monkeypatch, "set", "OvhSmsPassword") == 1


def test_set_pseudonymisation(ctl, monkeypatch, tmp_path, capsys):
    assert executer(ctl, monkeypatch, "set", "Pseudonymization", "on") == 2
    assert "--genkey" in capsys.readouterr().err
    assert executer(ctl, monkeypatch, "set", "Pseudonymization", "on", "--genkey") == 0
    texte = (tmp_path / "conf.d" / "local.conf").read_text()
    assert re.search(r'^FingerprintKey "[0-9a-f]{64}"$', texte, re.M)
    assert 'Pseudonymization "on"' in texte
    assert executer(ctl, monkeypatch, "set", "Pseudonymization", "off") == 0


def test_set_remplace_ancien_nom(ctl, monkeypatch, tmp_path):
    (tmp_path / "conf.d" / "local.conf").write_text('SmsAccount "sms-a-1"\nAutre\n')
    assert executer(ctl, monkeypatch, "set", "OvhSmsAccount", "sms-b-2") == 0
    assert (tmp_path / "conf.d" / "local.conf").read_text() == 'OvhSmsAccount "sms-b-2"\nAutre\n'
