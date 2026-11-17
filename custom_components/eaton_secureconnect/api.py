"""Staged client based on the authorized SecureConnect 2.1.8 APK.

Pairing and normal arm/disarm are separately gated and disabled by default.
No push registration, reset, bypass, force-arm or output control. Secrets stay
in this object's memory.
"""
import json
import re
import ssl
from urllib.error import HTTPError, URLError
from urllib.request import HTTPSHandler, HTTPRedirectHandler, ProxyHandler, Request, build_opener

BASE = "https://www.eatonsecureconnect.com/m2m-eaton-web/"
READ_PATHS = frozenset({
    "rest/pair/all/", "rest/mobileUser/panelStatus", "async/system/",
    "async/system/partitions/", "async/system/partitions/zones/",
    "async/faults/", "async/logs/?$top=20",
})
LIMIT = 2 * 1024 * 1024


def partition_path(partition_id, zones=False):
    if not isinstance(partition_id, str) or not re.fullmatch(r'[1-9][0-9]{0,2}', partition_id):
        raise ProbeError('Identificativo area non valido.')
    return f'async/system/partitions-{partition_id}/' + ('zones/' if zones else '')


def read_path_allowed(path):
    return path in READ_PATHS or re.fullmatch(r'async/system/partitions-[1-9][0-9]{0,2}/(?:zones/)?', path) is not None


class ProbeError(Exception):
    """Only fixed, safe messages; never includes a server response or secrets."""


def error_diagnostic(details, content_type, submitted=None):
    """Return categories and a scrubbed short error code, never the error body.

    Classify text in memory. Eaton's Angular client explicitly recognizes
    getJWT.arg0.login, loginPasswordPairIsNotExists and confirmation.needed.
    """
    import re
    diagnostic = {"response_format": "json" if isinstance(details, (dict, list)) else "non-json"}
    if not isinstance(details, dict):
        return diagnostic
    state = details.get('state')
    submitted = submitted or {}
    secrets = [v for v in submitted.values() if isinstance(v, str) and v]
    if (isinstance(state, str) and re.fullmatch(r'[A-Za-z][A-Za-z._-]{0,79}', state)
            and not any(v.lower() in state.lower() for v in secrets)):
        diagnostic['server_code'] = state
    # Never expose the text we examine or arbitrary field names.
    text = json.dumps(details, ensure_ascii=False).lower()
    if 'getjwt.arg0.login' in text:
        diagnostic['meaning'] = 'Il server segnala che il campo email/login non supera la validazione.'
    elif state == 'loginPasswordPairIsNotExists':
        diagnostic['meaning'] = 'La coppia email/password non è riconosciuta.'
    elif state == 'confirmation.needed':
        diagnostic['meaning'] = 'Il server richiede la conferma dell’account.'
    elif state == 'mobileApplication.IsNot.Found':
        diagnostic['meaning'] = 'La nuova installazione richiede un abbinamento alla centrale.'
    elif state == 'pinCode.IsNotValid':
        diagnostic['meaning'] = 'Codice temporaneo di abbinamento non valido; non è il PIN della tastiera.'
    elif state == 'pinCode.IsTimeout':
        diagnostic['meaning'] = 'Codice temporaneo scaduto; genera un nuovo codice con Abbina app.'
    elif 'getjwt.arg0.password' in text:
        diagnostic['meaning'] = 'Il server segnala che il campo password non supera la validazione.'
    elif 'applicationid' in text:
        diagnostic['meaning'] = 'La risposta fa riferimento all’identità dell’installazione (applicationId).'
    elif 'content-type' in text or 'media type' in text:
        diagnostic['meaning'] = 'La risposta segnala un problema di formato della richiesta.'
    else:
        diagnostic['meaning'] = 'Motivo non classificato; il codice HTTP non dimostra credenziali errate.'
    return diagnostic


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ProbeError("Redirect inatteso: richiesta fermata.")


class Client:
    def __init__(self, application_id, opener=None, allow_pairing=False, allow_controls=False):
        self.application_id = application_id
        self._jwt = None
        self.last_error = None
        self._allow_pairing = allow_pairing
        self._allow_controls = allow_controls
        self._opener = opener or build_opener(
            ProxyHandler({}), HTTPSHandler(context=ssl.create_default_context()), NoRedirect()
        )

    def clear(self):
        self._jwt = None

    def _request(self, method, path, body=None, client_token=None):
        self.last_error = None
        is_login = method == "PUT" and path == "rest/mobileUser/jwt/login"
        is_pairing = method == "PUT" and path == "rest/pair/" and self._allow_pairing
        is_control = (self._allow_controls and method == 'PUT'
            and re.fullmatch(r'async/system/partitions-[1-9][0-9]{0,2}/', path) is not None
            and isinstance(body, dict) and set(body) == {'state'}
            and body['state'] in ('fullset', 'unset'))
        if not is_login and not is_pairing and not is_control and (method != "GET" or not read_path_allowed(path) or body is not None):
            raise ProbeError("Operazione non consentita dal prototipo in sola lettura.")
        # Angular HttpClient uses this Accept value even for responseType:'text'.
        # Match the app rather than negotiating text/plain alone.
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/plain, */*"}
        if not is_login:
            if not self._jwt:
                raise ProbeError("Accesso richiesto.")
            headers["jwt"] = self._jwt
            if path.startswith("rest/"):
                headers["applicationId"] = self.application_id
            if is_pairing:
                headers.update({"platform": "Android", "mobileAppType": "eaton", "token": ""})
            if path.startswith("async/"):
                if not isinstance(client_token, str) or not client_token:
                    raise ProbeError("Abbinamento richiesto.")
                headers["client-token"] = client_token
        request = Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                          headers=headers, method=method)
        try:
            with self._opener.open(request, timeout=30) as response:
                raw = response.read(LIMIT + 1)
                if len(raw) > LIMIT:
                    raise ProbeError("Risposta troppo grande: richiesta fermata.")
        except HTTPError as error:
            # Eaton also uses structured business errors. Classify only states
            # observed in the APK; never return an arbitrary server message/body.
            try:
                details = json.loads(error.read(LIMIT + 1))
                state = details.get("state") if isinstance(details, dict) else None
            except (ValueError, OSError):
                details = None
                state = None
            finally:
                error.close()
            self.last_error = {"http_status": error.code,
                "operation": "login" if is_login else path,
                **error_diagnostic(details, error.headers.get('Content-Type', '') if error.headers else '', body)}
            if is_pairing and state in ('pinCode.IsNotValid', 'pinCode.IsTimeout'):
                raise ProbeError(self.last_error['meaning']) from None
            if state == "loginPasswordPairIsNotExists":
                self.clear()
                raise ProbeError("Eaton non riconosce la coppia email/password dell’app. Nessun nuovo tentativo automatico.") from None
            if state == "confirmation.needed":
                self.clear()
                raise ProbeError("Eaton richiede la conferma dell’account. Completa la verifica nell’app ufficiale.") from None
            if error.code in (401, 403):
                self.clear()
                raise ProbeError(f"Accesso rifiutato o scaduto (HTTP {error.code}). Nessun nuovo tentativo automatico.") from None
            raise ProbeError(f"Il servizio ha risposto HTTP {error.code}. Nessun nuovo tentativo automatico.") from None
        except (URLError, TimeoutError, OSError):
            raise ProbeError("Connessione non riuscita o scaduta; verifica TLS mantenuta attiva.") from None
        try:
            text = raw.decode("utf-8")
            if is_control and not text.strip():
                return None
            return text if is_login else json.loads(text)
        except (UnicodeError, ValueError):
            raise ProbeError("Formato della risposta inatteso.") from None

    def login(self, email, password):
        self.clear()
        result = self._request("PUT", "rest/mobileUser/jwt/login", {"login": email.strip(), "password": password})
        token = result.strip()
        # Some gateways JSON-encode a text response. Accept only a JWT-shaped value.
        if token.startswith('"'):
            try:
                token = json.loads(token)
            except ValueError:
                raise ProbeError("Formato della risposta di accesso inatteso.") from None
        import re
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", token):
            raise ProbeError("Il servizio non ha restituito un JWT riconoscibile.")
        self._jwt = token

    def pairings(self):
        result = self._request("GET", "rest/pair/all/")
        if not isinstance(result, list) or any(not isinstance(p, dict) for p in result):
            raise ProbeError("Formato elenco abbinamenti inatteso.")
        return result

    def pair(self, code):
        import re
        if not self._allow_pairing:
            raise ProbeError("Nuovo abbinamento non autorizzato in questa sessione.")
        if not isinstance(code, str) or not re.fullmatch(r'[A-Za-z0-9]{6}', code.strip()):
            raise ProbeError("Il codice temporaneo deve contenere sei caratteri alfanumerici.")
        response = self._request('PUT', 'rest/pair/', {
            'pairing-code': code.strip(), 'pairingName': 'HomeAssistantTest', 'primaryPairing': False,
        })
        if not isinstance(response, dict) or not isinstance(response.get('client-token'), str):
            raise ProbeError("Risposta di abbinamento inattesa. Non ripetere: l’abbinamento potrebbe essere stato creato.")
        return response

    def read_panel(self, client_token, progress=None):
        result = {}
        paths = {"system": "async/system/", "partitions": "async/system/partitions/",
                 "zones": "async/system/partitions/zones/", "faults": "async/faults/",
                 "logs": "async/logs/?$top=20"}
        for name, path in paths.items():
            if progress:
                progress(name)
            result[name] = self._request("GET", path, client_token=client_token)
        # The app uses per-partition zone endpoints on earlier partitioned panels.
        if result['system'].get('mode') == 'partitioned' and result['zones'] == []:
            groups = []
            combined = {}
            for partition in result['partitions']:
                partition_id = partition.get('id')
                if progress:
                    progress(f'zones_area_{partition_id}')
                zones = self.read_partition_zones(client_token, partition_id)
                if not isinstance(zones, list):
                    raise ProbeError('Formato elenco zone inatteso.')
                groups.append({'partition': partition_id, 'zones': zones})
                for zone in zones:
                    if isinstance(zone, dict) and 'id' in zone:
                        combined[str(zone['id'])] = zone
            result['zones'] = list(combined.values())
            result['zone_groups'] = groups
        return result

    def read_partition(self, client_token, partition_id):
        return self._request('GET', partition_path(partition_id), client_token=client_token)

    def read_partition_zones(self, client_token, partition_id):
        return self._request('GET', partition_path(partition_id, zones=True), client_token=client_token)

    def read_partitions(self, client_token):
        return self._request('GET', 'async/system/partitions/', client_token=client_token)

    def read_faults(self, client_token):
        return self._request('GET', 'async/faults/', client_token=client_token)

    def set_partition(self, client_token, partition_id, state):
        if not self._allow_controls or state not in ('fullset', 'unset'):
            raise ProbeError('Comando non abilitato o non previsto.')
        return self._request('PUT', partition_path(partition_id), {'state': state}, client_token=client_token)


# Keep only useful diagnostic fields: no server tokens, user identifiers, email,
# phone numbers, arbitrary unknown fields or server error bodies reach disk/UI.
SAFE_KEYS = frozenset({"id", "name", "state", "mode", "type", "desc", "timestamp",
    "events", "partitions", "zones", "faults", "logs", "system", "model", "version",
    "firmware", "firmware-version", "in-alarm", "is-fault", "time-to-exit", "omitted",
    "partition", "partition-id", "zone", "zone-id", "status", "open", "ready", "in-use", "partsets"})


def safe_data(value, depth=0):
    if depth > 10:
        return "[limite profondita]"
    if isinstance(value, dict):
        return {k: safe_data(v, depth + 1) for k, v in value.items() if k in SAFE_KEYS}
    if isinstance(value, list):
        return [safe_data(v, depth + 1) for v in value[:200]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        # Do not persist JWT-shaped strings even if a server placed one in a safe field.
        import re
        if re.search(r"[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}", value):
            return "[redatto]"
        return value[:500]
    return "[formato sconosciuto]"
