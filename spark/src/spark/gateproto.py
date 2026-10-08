"""The gate's protocol (Phase 2a; plan.md, *The front and the gate*), shared by the gate, the front and the CLI: the
gate's constants, its routes, each with the socket it is on and who may call it, and its messages' shapes.

JSON over HTTP/1.1 on the gate's two Unix sockets, which systemd holds (sockets.py): the status socket, for group
spark-users, and the control socket, for spark-admin. Every request body is a JSON object, and so is every answer
but a 2xx with nothing in it, such as a 204; one with a status other than 2xx is a GateRefusalBody. A stream
(`GET /v1/front/events`, `POST /v1/unload`, `POST /v1/make-room`, `POST /v1/load`, and `POST /v1/pin` when it loads
first) is NDJSON, one object a line; each but the events ends with its result line, or a refusal line in
GateRefusalBody's shape. Every time in a message is in Unix seconds, as time.time() gives it. A key appears only by
its name in the private key list, never as the key itself.

The standard library only, and nothing but definitions at import: the CLI imports it through gateclient, and
`spark status` and `spark launch` stay light (test_tested_against.py)."""

from __future__ import annotations

from typing import Any, Literal, NamedTuple, TypedDict, get_args

# The gate's constants: the values of website/design/phase-2a.md's Global Constraints, and that plan's own.
MAX_REQUEST_BYTES = 65536  # a request body above 64 KiB is answered 413, never parsed
# Every request is bounded by this, but the routes that wait: /v1/admit (its key's wait), /v1/front/events (kept
# open), /v1/load and /v1/pin (LOAD_CALL_TIMEOUT_S), and /v1/unload and /v1/make-room (as long as their drains take).
REQUEST_TIMEOUT_S = 5.0
# The gate's load call: 2a's healthCheckTimeout for llama-swap, 180 s, plus 20: the 5 s llama-swap takes to kill a
# stuck start, and a margin.
LOAD_CALL_TIMEOUT_S = 200.0
# The gate's unload call (the controller's ruling at Task 12's review): v257 answers once the engine has stopped, a
# stuck one after its unloadTimeout of 10 s and its kill, and stops queue in its one run loop, so room for four such.
# A timeout isn't llama_swap_down: the gate reads /running and counts the model stopping until it is gone.
UNLOAD_CALL_TIMEOUT_S = 60.0
PING_EVERY_S = 1.0  # /v1/front/events sends {op: "ping", at} this often
# The front counts the gate down once its events call has dropped and a new one hasn't been answered within this, and
# only then refuses new loads with gate_down.
GATE_DOWN_AFTER_S = 5.0
# Runs from the front's "drained" to the moment the unload call begins, and no further: a drain whose call hasn't
# begun this long after "drained" goes back to serving. From the call's start, only Task 16's rule ends a drain, at
# the model's leaving /running, since v257 never takes back an unload it took. It still outlasts
# UNLOAD_CALL_TIMEOUT_S, a sanity bound a test checks (the controller's rulings at Task 12's re-reviews).
DRAIN_GRACE_S = 90.0
# From the moment an unload call begins until /running shows its model gone, /v1/unload and /v1/make-room send a
# StoppingProgress this often, the first this long after the call begins.
STOPPING_EVERY_S = 15
# A model still stopping this long after its unload call began sends one alert, llama_swap_down's type with the
# why stuck_stopping: its engine may be stuck (the controller's ruling at Task 12's second re-review).
STUCK_STOPPING_S = 300
QUIET_S = 60  # make apply waits until no request has been in flight this long, by the front's counts
APPLY_DEADLINE_S = 900  # make apply's wait for quiet lasts at most this, then it offers "drain now"
ACTIVITY_EVERY_S = 1.0  # the gate writes its activity record, which the brake reads, this often
ACTIVITY_STALE_S = 3.0  # the brake takes an activity record older than this as missing
SESSIONS_PER_UID = 8  # the sessions one uid may hold at once
SESSION_TTL_S = 43200  # a session not renewed for 12 h ends, as one does when its process exits
REFUSAL_HISTORY = 50  # the refusals the gate keeps, for status's *recent*
NTFY_TIMEOUT_S = 5.0  # a publish to ntfy
BURST_WINDOW_S = 600  # of identical refusals the first goes at once, and the repeats within this go as one
BACK_UP_AFTER_S = 60  # back_up once a unit that was down has stayed up this long
# The brake's hold lifts by itself once memory has stayed above the warn line this long, if the reloads fit.
RELEASE_AFTER_S = 300
AUTO_RELEASE_EVERY_S = 3600  # at most one automatic release of the brake's hold in this long
NOTIFIER_EVERY_S = 300  # the failure notifier sends at most one alert per unit in this long
APPLY_RENEW_S = 15  # make apply renews its hold this often, from drain-all to the hold's end
# The gate ends an apply hold not renewed for this long, before begin as after; and the front drops the hold once
# the gate has been gone this long.
APPLY_LAPSE_S = 60
NOTIFIED_KEEP_S = 86400  # the gate's record of what it has notified drops what is older than this on every save
SESSION_HOLD_RENEW_S = 60  # `spark session hold` renews its session this often
GRACEFUL_S = 20  # uvicorn's graceful shutdown in the front and the gate, below their units' TimeoutStopSec=30
LLAMA_SWAP_HUNG_S = 10  # llama_swap_down once llama-swap hasn't answered this long while its unit stays active

Socket = Literal["status", "control"]
Callers = Literal["front", "users", "owner", "admin"]
SOCKETS: tuple[str, ...] = get_args(Socket)
# Who may call a route: `front` the front's uid only; `users` anyone the socket lets in (its group already limits who
# connects); `owner` a session's own uid, or a spark-admin uid to end one; `admin` a spark-admin uid, or root.
CALLERS: tuple[str, ...] = get_args(Callers)
SOCKET_GROUPS: dict[str, str] = {"status": "spark-users", "control": "spark-admin"}  # a socket's group: who connects


class Route(NamedTuple):
    socket: Socket
    method: str
    path: str  # Starlette's form: {id} and {model} are path parameters
    callers: Callers


ROUTES: tuple[Route, ...] = (
    Route("status", "POST", "/v1/admit", "front"),  # AdmitRequest → AdmitAnswer, always 200
    Route("status", "GET", "/v1/front/events", "front"),  # NDJSON, kept open: FrontEvent
    Route("status", "POST", "/v1/front/inflight", "front"),  # InflightSnapshot
    Route("status", "POST", "/v1/front/drained", "front"),  # DrainReport
    Route("status", "POST", "/v1/front/busy", "front"),  # DrainReport: an idle drain found a request in flight
    Route("status", "POST", "/v1/front/refused", "front"),  # FrontRefusal
    Route("status", "GET", "/v1/status", "users"),  # StatusView, filtered for the caller
    Route("status", "POST", "/v1/sessions", "owner"),  # SessionRequest → SessionGranted
    Route("status", "POST", "/v1/sessions/{id}/renew", "owner"),  # → SessionGranted
    Route("status", "DELETE", "/v1/sessions/{id}", "owner"),
    Route("control", "GET", "/v1/status", "admin"),  # the full StatusView
    # ModelRequest → NDJSON (the controller's rulings at Task 10's re-reviews): its head at once, a WaitProgress as it
    # starts waiting for the slot or for memory, a LoadProgress as the load starts, then LoadConfirmation; held up to
    # LOAD_CALL_TIMEOUT_S.
    Route("control", "POST", "/v1/load", "admin"),
    Route("control", "POST", "/v1/unload", "admin"),  # ModelRequest → NDJSON: UnloadCount at once, then UnloadDone
    # PinRequest → PinConfirmation for a model already loaded; a pin that loads first streams as /v1/load does: its
    # head at once, any WaitProgress, a LoadProgress as the load starts, then PinConfirmation; held up to
    # LOAD_CALL_TIMEOUT_S. (The controller's rulings at Task 10's re-reviews.)
    Route("control", "POST", "/v1/pin", "admin"),
    Route("control", "DELETE", "/v1/pin/{model}", "admin"),
    Route("control", "POST", "/v1/make-room/plan", "admin"),  # RoomPlanRequest → RoomPlanAnswer
    # MakeRoomRequest → NDJSON: a MakeRoomProgress as each model's drain begins, then MakeRoomAnswer (the controller's
    # ruling at Task 10's review: its head comes at once, as /v1/unload's does, so a gate that is down shows in time).
    Route("control", "POST", "/v1/make-room", "admin"),
    Route("control", "POST", "/v1/release", "admin"),  # ReleaseRequest → ReleaseAnswer
    Route("control", "GET", "/v1/quiet", "admin"),  # QuietAnswer
    Route("control", "POST", "/v1/drain-all", "admin"),  # apply's hold begins, persisted
    Route("control", "POST", "/v1/undrain-all", "admin"),  # one of the hold's ends
    Route("control", "POST", "/v1/apply/renew", "admin"),  # ApplyRenew → ApplyRenewed or ApplyEnded
    Route("control", "POST", "/v1/apply/begin", "admin"),  # ApplyBegin, before the first restart
    Route("control", "POST", "/v1/apply/end", "admin"),  # once llama-swap answers again: the hold's end
    Route("control", "GET", "/v1/logs/{model}", "admin"),  # ?n= → LogsAnswer; {model} a name or one of its roles
    Route("control", "POST", "/v1/canary", "admin"),  # CanaryRequest → CanaryAnswer
)

# There is no cancel route: the front drops its admit call when its client goes, and the gate takes the dropped call
# as the cancel.


class GateRefusalBody(TypedDict):
    """Every answer of the gate's with a status other than 2xx, and a refusal sent as a stream's line: `message`, one
    plain line, which the CLI shows as it is (gateclient.GateRefused), and the refusal's code (messages'), None where
    it has none, as for the 403 that says who may. Never OpenAI's {"error": {...}} shape, which the front's answers to
    clients use. /v1/admit alone answers a refusal with 200 and AdmitRefused. (The controller's ruling at Task 10's
    review.)"""

    message: str
    code: str | None

# Why a model is being unloaded, which each origin saves with its drain (the controller's rulings at Task 13's review):
# make-room, `spark unload`, an idle unload, or the gate's abort of a start past its deadline (late_start, Task 15).
# unknown: an unload with no why of the gate's, worded neutrally, naming no command: one restore found stopping with no
# record of it (the brake's, or one after damage), and the drains the gate finishes at a start after damage.
DrainWhy = Literal["make-room", "unload", "idle", "late_start", "unknown"]
DRAIN_WHY: tuple[str, ...] = get_args(DrainWhy)
FrontRefusalCode = Literal["model_not_found", "too_many_requests", "route_not_served", "draining"]
FRONT_REFUSAL_CODES: tuple[str, ...] = get_args(FrontRefusalCode)


# The status socket, the front's routes.

class AdmitRequest(TypedDict):
    model: str
    key: str  # the asking key's name
    deadline: float  # when the front received the request, plus the asking key's wait
    request_id: str


class Admitted(TypedDict):
    ok: Literal[True]
    model: str


class AdmitRefused(TypedDict):
    ok: Literal[False]
    code: str  # a refusal code of messages', and status and retry_after_s are that code's
    status: int
    message: str
    retry_after_s: int | None


AdmitAnswer = Admitted | AdmitRefused


class ModelEntry(TypedDict):
    """One model in the gate's model list, which the front serves from (Dan's decision of 2026-10-07)."""

    name: str
    roles: list[str]
    label: str
    resident: bool


class HelloEvent(TypedDict):
    op: Literal["hello"]
    gate_started_at: float
    applying: bool  # true: the front holds every new request, as on hold_all, so a restarted front holds again
    lapse_s: float  # the hold's bound, APPLY_LAPSE_S
    models: list[ModelEntry]


class StateEvent(TypedDict):
    """Sent with hello and on every change, a registry re-read among them."""

    op: Literal["state"]
    ready: list[str]  # model names
    starting: list[str]
    draining: list[str]
    models: list[ModelEntry]


class DrainEvent(TypedDict):
    op: Literal["drain"]
    model: str
    drain_id: str
    why: DrainWhy


class UndrainEvent(TypedDict):
    op: Literal["undrain"]
    model: str
    drain_id: str


class UnloadedEvent(TypedDict):
    op: Literal["unloaded"]
    model: str


class HoldAllEvent(TypedDict):
    """Apply's hold: the front holds every new request until release_all, or until the gate has been gone lapse_s."""

    op: Literal["hold_all"]
    lapse_s: float


class ReleaseAllEvent(TypedDict):
    op: Literal["release_all"]


class PingEvent(TypedDict):
    op: Literal["ping"]
    at: float


FrontEvent = (HelloEvent | StateEvent | DrainEvent | UndrainEvent | UnloadedEvent | HoldAllEvent | ReleaseAllEvent
              | PingEvent)


class InflightRequest(TypedDict):
    key: str  # the asking key's name
    started_at: float


class ModelInflight(TypedDict):
    count: int
    oldest_started_at: float | None
    last_end_at: float | None
    requests: list[InflightRequest]


class InflightSnapshot(TypedDict):
    """A whole snapshot, never a delta, so /v1/quiet and apply's question can name each request's asker."""

    seq: int
    front_started_at: float
    models: dict[str, ModelInflight]
    draining: list[str]  # the models the front holds as draining


class DrainReport(TypedDict):
    model: str
    drain_id: str


class FrontRefusal(TypedDict):
    """A refusal of the front's own, for status's *recent* and the refused notification."""

    at: float
    code: FrontRefusalCode
    model: str | None  # as the client asked for it; None where the request named none
    key: str  # the asking key's name
    message: str


# The status socket, everyone's.

class SessionRequest(TypedDict):
    model: str
    pid: int  # a live process of the caller's
    label: str


class SessionGranted(TypedDict):
    id: str
    expires_at: float


# The control socket, spark-admin's.

class ModelRequest(TypedDict):
    model: str


# Every progress line of a stream names its kind, and the CLI tells lines apart by it, never by their keys: a
# StoppingProgress's keys are a subset of a MakeRoomProgress's, in the same stream. A line without one is the result,
# or a refusal (GateRefusalBody). (The controller's ruling at Task 12's second re-review.)
ProgressKind = Literal["waiting", "loading", "unloading", "stopping"]
PROGRESS_KINDS: tuple[str, ...] = get_args(ProgressKind)


class WaitProgress(TypedDict):
    """A line of /v1/load's stream, and of a pin that loads, as the request starts waiting, and again whenever its
    reason changes, before its LoadProgress: so a queued load is never silent (the controller's ruling at Task 10's
    second re-review). The fields Task 7's `waiting` words its reason with: *Waiting its turn to load: … Gemma is
    loading, and one model loads at a time.*, *Waiting for memory: … It needs 41 GiB, and 18 GiB is free for a load.*
    Those a reason doesn't use are None."""

    kind: Literal["waiting"]
    model: str
    label: str
    why: WaitingWhy
    needed_gib: float | None  # memory: what the load needs
    free_gib: float | None  # memory: free for a load now
    loading_label: str | None  # slot: the model that holds the one-load slot
    release_waits_for_dan: bool | None  # brake: the pause lifts only when Dan releases it
    release_after_s: float | None  # brake: how long memory must stay above the warn line, when it lifts by itself


class LoadProgress(TypedDict):
    """A line of /v1/load's stream, as the load starts (none for a model already loaded): Task 7's `load_started`
    words it, *Loading the coder (24 s last time)…*."""

    kind: Literal["loading"]
    model: str
    label: str
    last_s: float | None  # the model's last load, None when there is none to go by


class LoadConfirmation(TypedDict):
    label: str
    seconds: float | None  # None for a model already loaded, as `messages.loaded` takes it
    idle_min: int | None  # None for an always-loaded model
    command: str


class UnloadCount(TypedDict):
    """/v1/unload's first line, at once: Task 7's `unloading` words it."""

    kind: Literal["unloading"]
    inflight: int


class UnloadDone(TypedDict):
    unloaded: Literal[True]


class PinRequest(TypedDict):
    model: str
    until: float | None  # None: no end


class PinConfirmation(TypedDict):
    """A pin's result, the fields Task 7's `pinned` words: *The coder stays loaded until 18:00 (loaded it first,
    24 s). `spark unpin coder` ends the pin.*"""

    label: str
    until: float | None  # None: no end
    loaded_s: float | None  # how long the pin's own load took; None when the model was loaded already
    command: str


class RoomBySize(TypedDict):
    size_gib: float


class RoomForAll(TypedDict):
    all: Literal[True]


RoomPlanRequest = RoomBySize | RoomForAll


class RoomPlanAnswer(TypedDict):
    plan_id: str
    plan: dict[str, Any]  # budget.RoomPlan, as JSON


class MakeRoomProgress(TypedDict):
    """A line of /v1/make-room's stream, as a model's drain begins: Task 7's `unloading` words it."""

    kind: Literal["unloading"]
    model: str
    label: str
    inflight: int


class StoppingProgress(TypedDict):
    """A line of /v1/unload's and /v1/make-room's streams, every STOPPING_EVERY_S from the moment a model's unload call
    begins until /running shows it gone, the first STOPPING_EVERY_S after the call begins, so Dan never waits in
    silence (the controller's rulings at Task 12's re-reviews): Task 30 words it, *Still stopping the coder:
    llama-swap hasn't finished its unload…*. Ctrl-C ends the wait, never the unload."""

    kind: Literal["stopping"]
    model: str
    label: str


class MakeRoomRequest(TypedDict):
    plan_id: str
    for_s: float | None  # None: until --done or a reboot


class MakeRoomAnswer(TypedDict):
    unloaded: list[str]
    free_gib: float
    hold_gib: float
    until: float | None


class ReleaseRequest(TypedDict):
    room: bool  # `spark make-room --done`
    brake: bool  # `make brake-release`


class RoomReleased(TypedDict):
    unused_gib: float
    total_gib: float
    next_label: str | None


class ReleaseAnswer(TypedDict):
    room: RoomReleased | None
    brake: bool
    reloading: list[str]  # labels


class QuietInflight(TypedDict):
    model: str
    model_label: str
    key_label: str
    age_s: float


class QuietAnswer(TypedDict):
    quiet_for_s: float
    last_label: str | None  # the model that answered last
    inflight: list[QuietInflight]


class ApplyRenew(TypedDict):
    since: float  # the hold's own start, so a renewal never starts a hold again


class ApplyRenewed(TypedDict):
    ok: Literal[True]


class ApplyEnded(TypedDict):
    ended: Literal[True]


class ApplyBegin(TypedDict):
    restarting: list[str]  # units


class LogsAnswer(TypedDict):
    lines: list[str]


class CanaryRequest(TypedDict):
    needle: str


class CanaryAnswer(TypedDict):
    found: list[str]  # where the needle is, never what surrounds it


# StatusView: `GET /v1/status` on either socket, and `spark status --json`'s shape, for 2b's menu bar.

STATUS_SCHEMA = 1  # raised when a field's meaning changes
# stopping: an unload call begun, and /running still lists the model (the controller's ruling at Task 12's second
# re-review), so a stop that hangs shows.
ModelState = Literal["ready", "starting", "draining", "stopping", "not_loaded"]
MODEL_STATES: tuple[str, ...] = get_args(ModelState)
WaitingWhy = Literal["memory", "brake", "slot", "dan", "restart", "llama_swap"]
WAITING_WHY: tuple[str, ...] = get_args(WaitingWhy)


class MemoryView(TypedDict):
    total_gib: float
    available_gib: float  # MemAvailable
    brake_gib: float
    warn_gib: float
    above_brake_gib: float
    reserve_gib: float
    owed_gib: float
    held_gib: float
    free_for_a_load_gib: float  # rule 9's figure, for the caller
    unaccounted_gib: float  # idle MemAvailable, less available, less the loaded models' footprints


class BrakeMarkView(TypedDict):
    at: float
    seen_gib: float


class ModelView(TypedDict):
    name: str
    label: str
    resident: bool
    footprint_gib: float
    state: ModelState
    inflight: int
    oldest_request_s: float | None
    last_use: float | None
    pinned: bool
    pinned_until: float | None  # the pin's end, None for a pin with no end (and with no pin), as `spark pin` words it
    sessions: list[str]  # the labels of the sessions that keep it
    brake_mark: BrakeMarkView | None


class WaitingView(TypedDict):
    key_label: str
    model: str
    waited_s: float
    wait_s: float
    why: WaitingWhy


class PausedView(TypedDict):
    since: float
    available_gib: float
    releases_at: float | None
    waits_for_dan: bool
    last_fired: float | None
    last_released: float | None


class HeldView(TypedDict):
    size_gib: float
    until: float | None  # None: until --done or a reboot


class PinView(TypedDict):
    model: str
    label: str
    until: float | None  # None: no end


class SessionView(TypedDict):
    id: str
    model: str
    label: str
    started_at: float
    expires_at: float


class RecentView(TypedDict):
    at: float
    text: str
    code: str | None
    model: str | None
    key_label: str | None


class BrakeHealth(TypedDict):
    status: str
    key_checked_at: float | None


class NtfyHealth(TypedDict):
    status: str
    failing_since: float | None


class EngineView(TypedDict):
    model: str
    port: int
    pid: int


class HealthView(TypedDict):
    front: str  # "ok", or what is wrong, in words; so are gate, llama_swap and the status of brake and ntfy
    gate: str
    brake: BrakeHealth
    llama_swap: str
    ntfy: NtfyHealth
    activity_age_s: float | None
    # Each engine on an engine port whose pid no ticket's start recorded this boot: the evidence of a load around the
    # gate.
    unticketed_engines: list[EngineView]
    no_ticket_refusals: int  # starts launch refused for want of a ticket since this boot: the backstop working


class ApplyingView(TypedDict):
    since: float
    restarting: list[str]


class StatusView(TypedDict):
    schema: int  # STATUS_SCHEMA
    host: str
    at: float
    memory: MemoryView
    models: list[ModelView]
    waiting: list[WaitingView]
    paused: PausedView | None
    held: HeldView | None
    pins: list[PinView]
    sessions: list[SessionView]
    recent: list[RecentView]
    health: HealthView
    applying: ApplyingView | None
    problems: list[str]
