"""Serialized blocking session: run ONLY through HA's executor."""
from threading import Lock
from time import monotonic
from .api import Client, ProbeError, PanelUnavailableError
from .model import normalize, check_command


class AuthError(ProbeError):
    pass


class Session:
    def __init__(self, data, client=None):
        self.data = data
        self.client = client or Client(data['application_id'], allow_controls=True)
        self.token = None
        self.lock = Lock()
        self._auth_retry_at = 0
        self._auth_retry_delay = 60

    def _connect(self):
        self.token = None
        self.client.login(self.data['username'], self.data['password'])
        matches = [p for p in self.client.pairings() if p.get('pairingName') == self.data['pairing_name']]
        if len(matches) != 1 or not matches[0].get('client-token'):
            raise AuthError('Abbinamento non trovato o ambiguo.')
        self.token = matches[0]['client-token']

    def _auth_failure(self):
        err = self.client.last_error or {}
        if err.get('server_code') in ('installer', 'appAccessDisabled'):
            return False
        return err.get('http_status') in (401, 403) or err.get('server_code') in (
            'loginPasswordPairIsNotExists', 'confirmation.needed', 'mobileApplication.IsNot.Found')

    def _permanent_auth_failure(self):
        err = self.client.last_error or {}
        return (err.get('server_code') in (
            'loginPasswordPairIsNotExists', 'confirmation.needed', 'mobileApplication.IsNot.Found')
            or (err.get('operation') == 'login' and err.get('http_status') == 401))

    def read(self):
        with self.lock:
            if monotonic() < self._auth_retry_at:
                raise ProbeError('Sessione Eaton temporaneamente non disponibile; nuovo tentativo automatico in attesa.')
            try:
                if self.token is None:
                    self._connect()
                try:
                    data = normalize(self.client.read_panel(self.token))
                except PanelUnavailableError:
                    raise
                except ProbeError:
                    if not self._auth_failure() or self._permanent_auth_failure():
                        raise
                    # One renewal per read, never retry a control request.
                    self._connect()
                    data = normalize(self.client.read_panel(self.token))
                self._auth_retry_at = 0
                self._auth_retry_delay = 60
                return data
            except PanelUnavailableError:
                raise
            except ProbeError:
                if self._permanent_auth_failure():
                    raise AuthError('Credenziali Eaton rifiutate o abbinamento non valido: verificare l’account.') from None
                if self._auth_failure():
                    # A rejected panel read does not prove a bad password.
                    # Keep HA polling with bounded login attempts instead of
                    # starting a reauth flow that permanently stops updates.
                    self.token = None
                    self.client.clear()
                    self._auth_retry_at = monotonic() + self._auth_retry_delay
                    self._auth_retry_delay = min(self._auth_retry_delay * 2, 900)
                    raise ProbeError('Accesso Eaton temporaneamente rifiutato; riconnessione automatica in attesa.') from None
                raise

    def command(self, area_id, action):
        with self.lock:
            if self.token is None:
                raise ProbeError('Aggiornare lo stato prima di inviare comandi.')
            areas = self.client.read_partitions(self.token)
            area = next((a for a in areas if str(a.get('id')) == area_id), None)
            faults = self.client.read_faults(self.token) if action == 'fullset' else []
            # Read target zones last, after the global reports, to avoid using
            # the cached coordinator snapshot to permit an arm command.
            zones = self.client.read_partition_zones(self.token, area_id) if action == 'fullset' else []
            if not check_command(area, action, zones, faults, partitions=areas):
                return
            try:
                self.client.set_partition(self.token, area_id, action)
            except ProbeError:
                raise ProbeError('Esito incerto: verificare nell’app Eaton prima di ripetere. Nessun reinvio automatico.') from None

    def close(self):
        with self.lock:
            self.token = None
            self.client.clear()
