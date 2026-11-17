"""Serialized blocking session: run ONLY through HA's executor."""
from threading import Lock
from .api import Client, ProbeError
from .model import normalize, check_command


class AuthError(ProbeError):
    pass


class Session:
    def __init__(self, data, client=None):
        self.data = data
        self.client = client or Client(data['application_id'], allow_controls=True)
        self.token = None
        self.lock = Lock()

    def _connect(self):
        self.client.login(self.data['username'], self.data['password'])
        matches = [p for p in self.client.pairings() if p.get('pairingName') == self.data['pairing_name']]
        if len(matches) != 1 or not matches[0].get('client-token'):
            raise AuthError('Abbinamento non trovato o ambiguo.')
        self.token = matches[0]['client-token']

    def _auth_failure(self):
        err = self.client.last_error or {}
        return err.get('http_status') in (401, 403) or err.get('server_code') in (
            'loginPasswordPairIsNotExists', 'confirmation.needed', 'mobileApplication.IsNot.Found')

    def read(self):
        with self.lock:
            try:
                if self.token is None:
                    self._connect()
                try:
                    return normalize(self.client.read_panel(self.token))
                except ProbeError:
                    if not self._auth_failure():
                        raise
                    # One renewal per read, never retry a control request.
                    self._connect()
                    return normalize(self.client.read_panel(self.token))
            except ProbeError:
                if self._auth_failure():
                    raise AuthError('Accesso Eaton scaduto o rifiutato.') from None
                raise

    def command(self, area_id, action):
        with self.lock:
            if self.token is None:
                raise ProbeError('Aggiornare lo stato prima di inviare comandi.')
            areas = self.client.read_partitions(self.token)
            area = next((a for a in areas if str(a.get('id')) == area_id), None)
            zones = self.client.read_partition_zones(self.token, area_id) if action == 'fullset' else []
            faults = self.client.read_faults(self.token) if action == 'fullset' else []
            if not check_command(area, action, zones, faults):
                return
            try:
                self.client.set_partition(self.token, area_id, action)
            except ProbeError:
                raise ProbeError('Esito incerto: verificare nell’app Eaton prima di ripetere. Nessun reinvio automatico.') from None

    def close(self):
        with self.lock:
            self.token = None
            self.client.clear()
