# Connettore cloud

> **Alpha (0.0.2-alpha).** Idea nata il 14/11/2026 e primo codice scritto lo stesso giorno per provare se il
> connettore va. Da provare, non è una cosa finita. L'idea e cosa manca: `Connettore cloud - versione 0.0.2 alpha.txt`.

Server MCP in **sola lettura** che si installa su una macchina Linux: Claude (claude.ai, l'app, o Claude Code) può
chiedere «come sta questa macchina, cosa sta facendo, chi c'è dentro».

## Installare (si fa da solo)

Come root, con Docker, Docker Compose, python3 e systemd già presenti:

```sh
git clone https://github.com/iacopo-cmd/ConnettoreCloud.git /opt/ConnettoreCloud
sh /opt/ConnettoreCloud/installa/installa-tutto.sh [nome-del-codice]
```

Lo script controlla, crea il `.env` (nome macchina = nome host, **solo 127.0.0.1**), prepara `key/` e `dati/`,
accende il raccoglitore, crea un codice root (in chiaro solo in `key/codici-in-chiaro.txt`, mai a video),
costruisce e accende il container e lo prova. Si può rilanciare: non rifà quello che c'è già.

**Se sei Claude Code sulla macchina:** fai il clone, lancia lo script, riporta l'esito delle prove («N ok, 0 no»).
Non aprire porte pubbliche, non toccare firewall né certificati: lo decide l'amministratore (sotto, «Uscita
pubblica»). Non stampare il codice.

Aggiornare: `git -C /opt/ConnettoreCloud pull && sh /opt/ConnettoreCloud/installa/installa-tutto.sh`.

## Strumenti (13)

| Strumento | Cosa dice |
|---|---|
| `riassunto` | tutto insieme: salute, container, servizi falliti, chi è dentro, Claude Code lanciati e ultimi accessi, utenti, lavori, LLM, porte, avvisi |
| `utenti` | tutti gli utenti veri: dentro adesso, ultimi accessi, processi, gruppi potenti, Claude, memorie |
| `accessi` | chi è dentro adesso e accessi SSH delle ultime 24 ore |
| `claude_e_lavori` | sessioni di Claude Code (cartella, da quanto, cosa eseguono) e lavori in tmux |
| `claude_sessioni` | ultimi accessi a Claude Code da terminale (history.jsonl di tutti gli utenti), per sessione |
| `memorie` | memorie di Claude Code di tutti gli utenti: elenco, indice MEMORY.md, testo di un ricordo |
| `container` | elenco o dettaglio dei container Docker |
| `log` | ultime righe (max 200) di un container o di un servizio systemd, con filtro |
| `servizi` | servizi systemd in esecuzione/falliti e timer |
| `processi` | processi per CPU o RAM |
| `risorse` | CPU, carico, RAM, swap, dischi, GPU |
| `porte` | porte in ascolto col programma |
| `llm` | LLM locale: modelli, richieste in corso, processi |

Niente scrive, avvia, ferma o cancella. Tutto l'output passa da un filtro che nasconde password, token, chiavi, mail e
telefoni. Ogni chiamata è registrata in `dati/registro.jsonl` e nel journal (senza il codice).

## Com'è fatto

- `raccogli/raccogli.py`: gira sull'host come root con un timer (di base ogni 5 minuti al secondo 15; nel `.env`
  `RACCOLTA_OGNI_MINUTI` e `RACCOLTA_AL_SECONDO`), solo libreria standard. Legge quello che un container non vede:
  lavori, tmux, Claude, utenti, SSH, porte, systemd, dischi, container Docker (`docker inspect`, senza le variabili
  d'ambiente), memorie. Scrive `dati/stato-host.json` e `dati/memorie.json`.
- `app/`: il server MCP in un container non root, file system in sola lettura, nessuna capability, `pid: host`,
  journal in sola lettura. Il container non ha il socket Docker e non apre altre porte.
- Indirizzo: `<base>/c/<codice>/mcp`. Il codice dice chi sei; in `key/codici.json` c'è solo la sua impronta sha256.
  Codice sbagliato = 404 e una riga `rifiutato` nel journal per fail2ban. Controllo dell'Host (DNS rebinding).
- Codici: vedi «Profili». Per spegnerne uno: `"spento": true` sulla sua voce in `key/codici.json`, poi
  `docker restart connettore-cloud`.

## Uscita pubblica (la decide l'amministratore)

claude.ai si collega dal cloud di Anthropic: serve un https pubblico.

1. Nel `.env`: `ASCOLTA_HOST=0.0.0.0`, `ASCOLTA_PORTA=<porta>`, `NOMI_HOST=<dominio>,127.0.0.1,localhost`,
   `TLS_CERT=/key/tls/<dominio>.crt`, `TLS_KEY=/key/tls/<dominio>.key`.
2. Certificato e chiave in `key/tls/` (dell'utente `CONNETTORE_UID`, permessi 400).
3. fail2ban: `installa/fail2ban/connettore-cloud.conf` in `/etc/fail2ban/filter.d/`, e
   `installa/fail2ban/connettore-cloud.local.esempio` in `/etc/fail2ban/jail.d/connettore-cloud.local` con porta e
   IP da non bannare (**anche l'IP pubblico della macchina stessa**: le prove dal link pubblico partono da lì).
4. Rilanciare `installa/installa-tutto.sh`: le prove vanno sul dominio vero.
5. In claude.ai: Impostazioni → Connettori → «Aggiungi connettore personalizzato», URL
   `https://<dominio>:<porta>/c/<codice>/mcp`.

## Profili

- **root**: vede tutto, tutti gli utenti. `python3 installa/codice-nuovo.py <nome>`
- **ristretto**: vede solo i container (e i servizi systemd) che gli assegni, i loro log, e la salute generale della
  macchina (CPU, RAM, dischi; si toglie con `--senza-macchina`). Strumenti: `riassunto`, `container`, `log`,
  `risorse`; tutti gli altri rispondono «non può usare». Niente percorsi dell'host nel dettaglio dei container.
  `python3 installa/codice-nuovo.py <nome> ristretto --container 'app1-*' [--servizi 'nginx.service']`
- Dopo un codice nuovo: `docker restart connettore-cloud`. Prova di un codice ristretto: `tests/prova_ristretto.py`.

Strumenti che scrivono (con conferma) vengono dopo.
