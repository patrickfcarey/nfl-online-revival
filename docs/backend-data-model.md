# What the backend has to remember

Derived from the client's own protocol vocabulary. Every **column name** below
is a real key string in `madden_SLUS_207.52` with a recorded address; the
**types**, and the splitting of the `*0`/`*1` suffix pairs into a side table,
are inference.

## The login flow this serves

The client drives everything; the server only answers. After `skey` succeeds
the server sends **nothing** — the client synthesises a local event and waits
for the user.

```
client @dir                    -> server @dir (ADDR, PORT, SESS, MASK)
client reconnects to ADDR:PORT
client addr (its own address)  -> no reply expected
client skey (SKEY=$hex)        -> server skey (SKEY=$hex16, code 0)   state: idle
client acct (registration)     -> server acct (code 0)                state: acct
client auth (NAME, PASS)       -> server auth (code 0, NAME, ADDR, PERSONAS, OPTS)
client pers (PERS=name)        -> server pers (code 0, PERS)
client sele "ROOMS=1 USERS=1 RANKS=1 MESGS=1"
                               -> server sele (code 0, MORE, SLOTS)
                                  then pushes +rom / +usr / +rnk / +msg / +pop / +who
```

**The status field is four ASCII characters, not a number.** Zero is success;
anything else is an error tag looked up at `0x00573c20`: `inam` `elen` `dupl`
`mail` `pass` `tosa` `born` `gend` `spam` `filt` `many` `tooy`. Tournament
errors use their own set at `0x0056d4dc`: `full` `dupl` `strt` `uniq` `miss`.

**Passwords never arrive in the clear and cannot yet be recovered.** When a
non-zero `SKEY` has been negotiated the client encrypts `PASS`, prefixes it with
`~`, and sends that. The cipher at `0x0044ef28` has not been reversed. With an
all-zero `SKEY` the client instead XOR-masks against `MASK`, falling back to the
built-in default `"GS"` at `0x006089a0` when `MASK` is absent. Either way the
server stores what it is given and compares on re-presentation; it does not need
the plaintext.

## Schema

```sql
CREATE TABLE account(          -- keys at 0x00609248
  NAME TEXT PRIMARY KEY, PASS TEXT, MAIL TEXT, PMAIL TEXT,
  BORN TEXT, GEND TEXT, SPAM TEXT, TOS INTEGER, CPAT INTEGER,
  ALTS INTEGER DEFAULT 4, OPTS TEXT, CDEV TEXT, AGE INTEGER, CHNG TEXT);

CREATE TABLE persona(          -- PERSONAS is comma-separated, max 4, <=12 chars
  PERS TEXT PRIMARY KEY,
  NAME TEXT NOT NULL REFERENCES account(NAME) ON DELETE CASCADE);

CREATE TABLE rank_stat(        -- 'snap' paging: FIRST/COUNT/RANGE
  PERS TEXT, CHAN TEXT, STAT TEXT, WEEK INTEGER,
  RANK INTEGER, SKIL INTEGER, INFO TEXT,
  PRIMARY KEY(PERS, CHAN, STAT, WEEK));

CREATE TABLE game(             -- 'rank' report, keys at 0x0011a378
  GAMEID INTEGER PRIMARY KEY, WHEN_ INTEGER, REPT INTEGER, AUTH TEXT,
  DISC INTEGER, QLEN INTEGER, DSYNC INTEGER,
  TOURNEYID INTEGER, ROUND INTEGER);

CREATE TABLE game_side(        -- the NAME0/NAME1 pairs, normalised
  GAMEID INTEGER REFERENCES game(GAMEID) ON DELETE CASCADE,
  SIDE INTEGER, NAME TEXT, TEAM INTEGER, WEIGHT INTEGER,
  SCORE INTEGER, DSCORE INTEGER, INTS INTEGER, SACKS INTEGER, CHT INTEGER,
  RUSHATT INTEGER, RUSHYDS INTEGER, PASSATT INTEGER, PASSYDS INTEGER,
  TURNOVERS INTEGER, PRIMARY KEY(GAMEID, SIDE));

CREATE TABLE tourney(          -- #cre / #new
  TID INTEGER PRIMARY KEY, TNAME TEXT UNIQUE, PASS TEXT, TEXT_ TEXT,
  SIZE INTEGER, ROUND INTEGER, START INTEGER, LIFE INTEGER,
  INACT INTEGER, UNIQ INTEGER, UFLAG INTEGER, SFLAG INTEGER,
  UPARM TEXT, ROLLOVER INTEGER, MOD INTEGER);

CREATE TABLE tourney_member(   -- #joi / #lea / #mem
  TID INTEGER REFERENCES tourney(TID) ON DELETE CASCADE,
  PERS TEXT, TEAM INTEGER, RANK INTEGER, UPARM TEXT, IDX INTEGER,
  PRIMARY KEY(TID, PERS));

CREATE TABLE tourney_node(     -- bracket: TREE/RND/G/R/I/U0/U1/W
  TID INTEGER, RND INTEGER, IDX INTEGER,
  U0 TEXT, U1 TEXT, W TEXT, G INTEGER, R INTEGER, I INTEGER, ST2 INTEGER,
  PRIMARY KEY(TID, RND, IDX));

CREATE TABLE buddy(            -- ROST / RADD / RDEL, the separate buddy service
  USER TEXT, LIST TEXT, LRSC TEXT, GROUP_ TEXT, ID INTEGER,
  PRIMARY KEY(USER, LIST));

CREATE TABLE message(          -- SEND / RECV, offline delivery
  ID INTEGER PRIMARY KEY, USER TEXT, FUSR TEXT,
  SUBJ TEXT, BODY TEXT, TIME INTEGER, SIZE INTEGER);

CREATE TABLE room(             -- 'room' definitions
  NAME TEXT PRIMARY KEY, DESC TEXT, PASS TEXT, MAX INTEGER, CHAN TEXT);
```

`TNAME` is `UNIQUE` because the client has a `dupl` error tag for it, and
`strt` ("that tournament has started") only means anything if tournaments
outlive a session.

## Durable vs ephemeral

**Durable:** accounts, personas, rank stats, games and their sides, tournaments
and brackets, buddies, offline messages, room definitions.

**Ephemeral, in memory only:** `SESS` and `SKEY` (per connection), room
occupancy and `SLOTS`, presence (`PRES`/`SHOW`/`STAT`), keepalive timers, the
snapshot paging cursors (`INDEX`/`START`/`RANGE`/`MORE`), and the matchmaking
queue. Nothing here is re-read after a reconnect.

## Storage

**`sqlite3`**, one file, `journal_mode=WAL`, `foreign_keys=ON`. It offers
transactions, the `UNIQUE` constraint the `dupl` error tag implies, and the
`ORDER BY`/`LIMIT` that `snap`'s `FIRST`/`COUNT`/`RANGE` paging maps onto
directly. At the expected scale — dozens of users — one connection behind a
lock is ample.

It also happens to be in the standard library, which suits how this repo is
deployed: `tar | ssh` onto the rig, no pip and no virtualenv to go wrong
mid-session. That is a convenience of the current setup, **not a constraint
anyone has agreed to** — if a real dependency earns its place later (a proper
async server, a migration tool), nothing here forbids it.

## A separate service

`BUDDY_URL` and `BUDDY_PORT` (`0x005afee0`, `0x005afef0`) are read out of a
server message, so the buddy list is a **second endpoint**, not part of this
one. Its verbs are XMPP-shaped — `ROST` `RADD` `RDEL` `SEND` `RECV` `PGET`
`PSET` `PADD` `PDEL` — and it can be stubbed until the main service works.
