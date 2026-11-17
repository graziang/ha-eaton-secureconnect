"""Protocol normalization and event cursors, independent of Home Assistant."""
from .api import ProbeError, safe_data


def normalize(raw):
    if not isinstance(raw, dict) or not isinstance(raw.get('system'), dict):
        raise ProbeError('Risposta centrale non valida.')
    result = {'system': safe_data(raw['system'])}
    for key in ('partitions', 'zones', 'faults', 'logs'):
        values = raw.get(key)
        if not isinstance(values, list) or any(not isinstance(v, dict) for v in values):
            raise ProbeError('Risposta centrale incompleta.')
        result[key] = safe_data(values)
    for key in ('partitions', 'zones'):
        ids = [str(v.get('id', '')) for v in result[key]]
        if any(not i.isdigit() for i in ids) or len(ids) != len(set(ids)):
            raise ProbeError('Identificativi duplicati o mancanti.')
        result[key] = {str(v['id']): v for v in result[key]}
    if not result['partitions']:
        raise ProbeError('Nessuna area disponibile.')
    result['zone_partitions'] = {
        str(z['id']): str(g['partition'])
        for g in raw.get('zone_groups', []) for z in g['zones']
    }
    return result


def contact_state(zone):
    return {'open': True, 'closed': False}.get(zone.get('state'))


def fault_state(partitions, faults):
    """Follow the panel's explicit area fault flags, not the fault-list length.

    SecureConnect also returns setting impediments in /faults/. Its own UI
    renders the area `is-fault` flag. An unrecognised response stays unknown.
    """
    flags = [area.get('is-fault') for area in partitions.values()]
    if any(flag is True for flag in flags):
        return True
    if flags and all(flag is False for flag in flags):
        return False
    if not faults:
        return False
    return None


def alarm_state(area):
    if area.get('in-alarm') is True:
        return 'triggered'
    try:
        if float(area.get('time-to-exit', 0)) > 0:
            return 'arming'
    except (ValueError, TypeError):
        return None
    return {'unset': 'disarmed', 'fullset': 'armed_away'}.get(area.get('state'))


def check_command(area, action, zones, faults):
    if action not in ('fullset', 'unset') or not area:
        raise ProbeError('Comando o area non validi.')
    if area.get('in-alarm') is not False:
        raise ProbeError('Allarme attivo o stato ignoto: usare l’app Eaton.')
    if area.get('state') not in ('unset', 'fullset') or alarm_state(area) == 'arming':
        raise ProbeError('Stato transitorio o sconosciuto: aggiornare prima di riprovare.')
    if area['state'] == action:
        return False
    if action == 'fullset' and (faults != [] or not zones or any(
        z.get('state') != 'closed' or z.get('omitted') is not False for z in zones
    )):
        raise ProbeError('Inserimento bloccato: guasti, area vuota o sensori aperti/esclusi/sconosciuti.')
    return True


class EventCursor:
    """Baseline on startup/reconnect; emit only new IDs in overlapping windows.

    Missing overlap means a possible gap: rebaseline, never replay a stale batch.
    No persistence is needed because each startup intentionally baselines.
    """
    def __init__(self):
        self.seen = None
        self.gap = False

    def reset(self):
        self.seen = None

    def consume(self, logs):
        records = {str(v['id']): v for v in logs if v.get('id') is not None}
        ids = set(records)
        self.gap = bool(self.seen and ids and not ids.intersection(self.seen))
        fresh = [] if self.seen is None or self.gap else [v for k, v in records.items() if k not in self.seen]
        self.seen = ids
        return list(reversed(fresh))
