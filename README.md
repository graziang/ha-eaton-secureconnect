# Eaton SecureConnect per Home Assistant

Custom integration sperimentale, basata sul protocollo dell’app SecureConnect già verificato nel prototipo. Richiede Home Assistant **2026.11 o successivo**. Non è un’integrazione ufficiale Eaton o HA.

Aggiungendo **Eaton SecureConnect** da Impostazioni → Dispositivi e servizi vengono creati automaticamente:

- Un dispositivo centrale con un `alarm_control_panel` per ogni area.
- Un dispositivo per ogni zona, collegato alla centrale, con `binary_sensor` e nome Eaton. Si possono assegnare stanze e personalizzare nomi in HA.
- Un sensore diagnostico dei guasti.
- Un’entità `event` e l’evento bus `eaton_secureconnect_event` per i nuovi record dello storico.

Le zone e le aree aggiunte successivamente vengono scoperte al polling seguente. Gli ID stabili conservano le personalizzazioni; una zona mancante diventa non disponibile. Il tipo di sensore è genericamente apertura: non si deduce se sia un PIR dal nome. L’area di allarme Eaton è un attributo, non viene confusa con la stanza HA.

## Installazione

Da **Impostazioni → Marketplace → Repository personalizzati**, aggiungere l’URL GitHub del progetto come **Integrazione** e installare. Collegare GitHub se richiesto. Al termine aprire la configurazione dell’integrazione e seguire eventuali indicazioni di riavvio mostrate da HA.

Servono email/password Eaton, UUID di un abbinamento dedicato già creato e il suo nome. Questa prima versione riutilizza un abbinamento esistente: non include ancora la procedura per crearne uno nuovo in HA. Non copia token del telefono e non usa SIA/BMS. Inserire i segreti direttamente nell’interfaccia HA. HA li conserva nella config entry e nei relativi backup; i JWT/client-token restano in RAM.

In Opzioni si può abilitare il controllo dell’allarme. Il salvataggio delle opzioni non invia comandi. Le azioni native sono `alarm_control_panel.alarm_arm_away` (inserimento completo dell’area scelta) e `alarm_control_panel.alarm_disarm`. Non sono implementati parziali, esclusioni, forzature, reset o sirene. I comandi sono disabilitati inizialmente; abilitandoli si autorizzano gli utenti/automazioni HA che possono controllare l’entità, senza un secondo PIN Eaton.

Prima dell’inserimento si rileggono area, guasti e sensori: area vuota, guasti o sensori aperti/esclusi/ignoti bloccano il comando. Allarme attivo o stato transitorio/ignoto richiedono l’app ufficiale. Un comando viene inviato una sola volta e seguito da lettura: risposta ambigua o stato non ancora raggiunto producono un errore esplicito, senza reinvio o stato ottimistico.

## Automazioni

Nell’editor HA scegliere come trigger **Stato**, selezionare il sensore importato e il passaggio da `off` a `on`. Specificare entrambi evita di scambiare il ripristino da `unavailable` per un’apertura. Esempio da adattare all’ID effettivo:

```yaml
alias: Segnala apertura finestra
triggers:
  - trigger: state
    entity_id: binary_sensor.finestra_camera
    from: "off"
    to: "on"
actions:
  - action: persistent_notification.create
    data:
      title: Finestra aperta
      message: La finestra della camera è stata aperta.
mode: single
```

Per gli eventi si può usare l’entità evento oppure il trigger evento `eaton_secureconnect_event`. Payload: `entry_id` della config entry e `log` con `id`, `type`, `desc`, `events` (timestamp e altri campi disponibili). Gli ID `partition` nello storico possono essere zero-based: vengono conservati senza conversioni arbitrarie. Non usare solo descrizioni tradotte come contratto stabile per azioni critiche.

Non vengono riprodotti gli eventi già presenti al caricamento o dopo un errore di connessione. Con finestre dello storico senza sovrapposizione si segnala `event_gap` nel sensore guasti e si riparte da una nuova baseline. Questo evita azioni tardive, ma può perdere eventi. Non è una coda affidabile né un archivio completo.

## Limiti e verifica

Polling cloud predefinito 30 secondi, configurabile 15–300. Ogni aggiornamento comporta più richieste sequenziali, quindi il ritardo può superare l’intervallo configurato. Un contatto che apre e chiude tra due letture può sfuggire; lo storico della centrale non garantisce un record per ogni apertura. Non adatto ad automazioni che richiedono rilevamento istantaneo o garantito. Disponibilità e limiti di frequenza dell’API non documentata restano da verificare nel tempo.

Il codice usa coordinator condiviso, executor per I/O bloccante, config flow/reauth, registry e piattaforme native HA. Riferimenti: [coordinator](https://developers.home-assistant.io/docs/integration_fetching_data/), [allarmi](https://developers.home-assistant.io/docs/core/entity/alarm-control-panel/), [config flow](https://developers.home-assistant.io/docs/config_entries_config_flow_handler/).

Test con HA 2026.11.1 e trasporto simulato: setup/unload, registrazione dispositivi, discovery incrementale, eventi/deduplicazione, errore comunicazione, config flow, controlli prima del comando e nessun retry di scritture. Non dimostrano ancora apertura fisica, comandi reali e stabilità prolungata sulla centrale. Nessuna prova automatica aziona l’impianto.

```sh
python -m pip install homeassistant==2026.11.1 pytest pytest-asyncio
python -m pytest tests/ha_integration -q
```

Rollback: rimuovere l’integrazione dalla UI; per rimuovere il codice, eliminare soltanto la cartella `eaton_secureconnect` e riavviare Core. Nessuna configurazione della centrale viene modificata dall’installazione.

## Licenza

Codice e icona generica distribuiti con licenza MIT. Il nome Eaton identifica il servizio compatibile; il progetto non è affiliato a Eaton.
