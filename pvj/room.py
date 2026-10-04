# SPDX-FileCopyrightText: 2026 NXLX.Systems and contributors
# SPDX-License-Identifier: Apache-2.0
"""The room: groups of projectors, scenes a person taps once, and the state of each group at a glance.

A group is a named set of the projectors added under System > Projectors ("Main wall", "Painting wall"); "All" is
every projector and needs no entry. A scene says, per group, power on, off or leave, an input (the projector's
own input code, shown by its label), picture mute and sound mute or leave, and one thing the box itself does.
Groups and scenes live in settings["room"], read with defaults (no schema change).

How a scene or a group button reaches the projectors:
* One worker thread per projector at most (so never more than projector.MAX_PROJECTORS), each sending one
  command at a time through the same PJLink client as the Projectors card: the private-address check before
  every command, one command at a time per projector, the 10 second deadline.
* Nobody waits: the request answers "started" and the result is read from GET /api/room.
* The order per projector is power, input, picture mute, sound mute. A projector that says "unavailable"
  (warming up, cooling down) is asked again for as long as Phase 1's input retry lasts; the input change itself
  goes through Monitor.set_input, so it is that retry, and the last input chosen is the one that stands.
* A second scene replaces the first: what the first had not sent yet is dropped, an input change still being
  retried for it is stopped, and the same thread then sends the newer choice, so an older command can never
  follow a newer one. A group button only replaces its own kind of step (Off replaces everything).

What the box does in a scene is a short list of existing API actions (BOX below); a later feature adds one entry.
"""

import copy
import re
import threading
import time
import uuid

from . import projector as projector_mod
from .api import ApiError, MEDIA_EXTENSIONS, bad, valid_name

MAX_GROUPS = 8
MAX_SCENES = 24
NAME_MAX = 32
ALL = "all"
POWER = ("leave", "on", "off")
MUTE = ("leave", "mute", "unmute")
ORDER = ("power", "input", "picture", "sound")       # the order of the steps for one projector
GROUP_ACTIONS = ("on", "off", "mute", "unmute", "mute_picture", "unmute_picture", "mute_sound", "unmute_sound", "input")
ROOM_DEVICE = {"id": "room", "name": "Room", "role": "live"}     # what the box does in a scene runs as a presenter
GRACE = 25.0                 # seconds more than the monitor's own retry time that an input change may take
_ID = re.compile(r"[0-9a-f]{8}")
_INPUT = re.compile(r"[1-5][1-9]")


class RoomError(Exception):
    pass


def default_room():
    return {"groups": [], "scenes": []}


# --- what the box does in a scene -----------------------------------------------------------------------------
def _box_file(b):
    name, loop = b.get("file"), b.get("loop", True)
    if not valid_name(name) or not name.lower().endswith(MEDIA_EXTENSIONS):
        raise RoomError("choose a clip for the scene to play")
    if not isinstance(loop, bool):
        raise RoomError("loop must be true or false")
    return {"file": name, "loop": loop}


def _box_pad(b):
    pad = b.get("pad")
    if (not isinstance(pad, list) or len(pad) != 2 or not all(isinstance(x, int) and not isinstance(x, bool) for x in pad)
            or not (0 <= pad[0] <= 9 and 0 <= pad[1] <= 11)):
        raise RoomError("pad must be [bank, index]")
    return {"pad": list(pad)}


def _box_stream(b):
    sid = b.get("stream")
    if not isinstance(sid, str) or not _ID.fullmatch(sid):
        raise RoomError("choose a saved stream for the scene to play")
    return {"stream": sid}


# action -> (a clean copy of its own values, the API calls it makes, whether it puts a picture on the screen).
# Every call goes through Api.handle as a presenter, so it is checked exactly as a tap in the panel is.
BOX = {
    "leave": (lambda b: {}, lambda b: [], False),
    "file": (_box_file, lambda b: [("/api/play", {"file": b["file"], "loop": b["loop"]})], True),
    "pad": (_box_pad, lambda b: [("/api/play", {"pad": list(b["pad"])})], True),
    "stream": (_box_stream, lambda b: [("/api/play", {"stream": b["stream"]})], True),
    "stop": (lambda b: {}, lambda b: [("/api/control", {"action": "stop"})], False),
    "blackout": (lambda b: {}, lambda b: [("/api/blackout", {"on": True})], False),
}


def validate_box(b):
    if b is None:
        b = {"action": "leave"}
    if not isinstance(b, dict) or not isinstance(b.get("action", "leave"), str) or b.get("action", "leave") not in BOX:
        raise RoomError("what the box does must be one of: %s" % ", ".join(BOX))
    action = b.get("action", "leave")
    return dict(BOX[action][0](b), action=action)


# --- groups and scenes: checks ----------------------------------------------------------------------------------
def _name(value, what, seen):
    if (not isinstance(value, str) or not 1 <= len(value.strip()) <= NAME_MAX
            or any(projector_mod._unprintable(ch) for ch in value)):
        raise RoomError("give the %s a name of up to %d plain characters" % (what, NAME_MAX))
    name = value.strip()
    if name.lower() in seen:
        raise RoomError("there is already a %s called %s" % (what, name))
    seen.add(name.lower())
    return name


def _id(given, seen):
    if given is None or given == "":
        given = uuid.uuid4().hex[:8]
    if not isinstance(given, str) or not _ID.fullmatch(given) or given in seen:
        raise RoomError("bad or repeated id")
    seen.add(given)
    return given


def _row(r):
    """One line of a scene: what a group gets."""
    if not isinstance(r, dict):
        raise RoomError("a scene's groups must be objects")
    group = r.get("group")
    if group != ALL and not (isinstance(group, str) and _ID.fullmatch(group)):
        raise RoomError("a scene names its groups by id, or \"all\"")
    power, code = r.get("power", "leave"), r.get("input", "")
    picture, sound = r.get("picture", "leave"), r.get("sound", "leave")
    if not isinstance(power, str) or power not in POWER:
        raise RoomError("power must be on, off or leave")
    if not isinstance(code, str) or (code and not _INPUT.fullmatch(code)):
        raise RoomError("input must be a projector input such as 31, or empty")
    for v in (picture, sound):
        if not isinstance(v, str) or v not in MUTE:
            raise RoomError("a mute must be mute, unmute or leave")
    if power == "off" and (code or picture != "leave" or sound != "leave"):
        raise RoomError("a group that is switched off cannot also get an input or a mute")
    return {"group": group, "power": power, "input": code, "picture": picture, "sound": sound}


def validate(value):
    """A clean settings["room"] from untrusted input (the panel, or a settings file). Raises RoomError. Only the
    form is checked: a projector, group, clip or stream that no longer exists is skipped when a scene runs."""
    if value is None:
        return default_room()
    if not isinstance(value, dict):
        raise RoomError("room must be an object")
    groups, scenes = value.get("groups", []), value.get("scenes", [])
    if not isinstance(groups, list) or len(groups) > MAX_GROUPS:
        raise RoomError("at most %d groups" % MAX_GROUPS)
    if not isinstance(scenes, list) or len(scenes) > MAX_SCENES:
        raise RoomError("at most %d scenes" % MAX_SCENES)
    out, ids, names = default_room(), set(), {ALL}
    for g in groups:
        if not isinstance(g, dict):
            raise RoomError("a group must be an object")
        pids = g.get("projectors")
        if (not isinstance(pids, list) or not 1 <= len(pids) <= projector_mod.MAX_PROJECTORS
                or not all(isinstance(p, str) and _ID.fullmatch(p) for p in pids) or len(set(pids)) != len(pids)):
            raise RoomError("a group has 1 to %d different projectors" % projector_mod.MAX_PROJECTORS)
        out["groups"].append({"id": _id(g.get("id"), ids), "name": _name(g.get("name"), "group", names), "projectors": list(pids)})
    ids, names = set(), set()
    for s in scenes:
        if not isinstance(s, dict):
            raise RoomError("a scene must be an object")
        rows = s.get("groups", [])
        if not isinstance(rows, list) or len(rows) > MAX_GROUPS + 1:
            raise RoomError("a scene has at most %d groups" % (MAX_GROUPS + 1))
        clean = [_row(r) for r in rows]
        if len({r["group"] for r in clean}) != len(clean):
            raise RoomError("a scene names each group once")
        out["scenes"].append({"id": _id(s.get("id"), ids), "name": _name(s.get("name"), "scene", names),
                              "groups": clean, "box": validate_box(s.get("box"))})
    return out


# --- sending ---------------------------------------------------------------------------------------------------
class _Step:
    """One thing one projector is asked to do. state: todo, waiting (an input change the monitor is retrying),
    done, failed, skipped (an earlier step failed) or dropped (a newer choice replaced it)."""

    def __init__(self, kind, want, label=""):
        self.kind, self.want, self.label = kind, want, label
        self.state, self.text, self.since = "todo", "", None

    @property
    def live(self):
        return self.state in ("todo", "waiting")

    def doing(self):
        if self.kind == "power":
            return "switching on" if self.want else "switching off"
        if self.kind == "input":
            return "waiting to switch to %s" % self.label
        return "waiting to %s the %s" % ("mute" if self.want else "unmute", self.kind)

    def did(self):
        if self.kind == "power":
            return "on" if self.want else "off"
        if self.kind == "input":
            return "input %s" % self.label
        if self.kind == "picture":
            return "picture muted" if self.want else "picture shown"
        return "sound muted" if self.want else "sound on"


class _Slot:
    """What one projector still has to do, and the one thread doing it."""

    def __init__(self, pid):
        self.pid = pid
        self.steps = []                       # in ORDER
        self.cancel = threading.Event()       # set when a newer choice arrives: the command not yet sent is not sent
        self.wake = threading.Event()
        self.thread = None
        self.handed = None                    # an input change of ours that the monitor is retrying
        self.powered = 0.0                    # time.monotonic() of our last "power on" that the projector took


class Room:
    def __init__(self, api, log=print, tick=0.25):
        self.api, self.log, self.tick = api, log, tick
        self.lock = threading.Lock()          # never held while talking to a projector or the player
        self._slots = {}                      # projector id -> _Slot, only while it has work
        self._scene_job = None                # the scene applied last
        self._group_jobs = {}                 # group id -> the group button pressed last

    # -- reading the settings --
    @property
    def monitor(self):
        return self.api.projectors

    def config(self):
        cfg = self.api.settings.data.get("room") or {}
        return {"groups": list(cfg.get("groups") or []), "scenes": list(cfg.get("scenes") or [])}

    def enabled(self):
        return self.api.registry.enabled("room") and self.api.registry.enabled("projector")

    def _need(self):
        if not self.enabled():
            raise ApiError(409, "turn on the Room module in System first")

    def _projectors(self):
        return list(self.api.settings.data.get("projectors") or [])

    def _entry(self, pid):
        """The projector's settings entry, or None once the Room or Projector module is off or it was removed."""
        return self.monitor._entry(pid) if self.enabled() else None

    @staticmethod
    def _label(entry, code):
        return (entry.get("labels") or {}).get(code) or projector_mod.input_name(code)

    def _members(self, group, entries):
        """(name, [projector entries]) of a stored group or of "all"; projectors that were removed are skipped."""
        if group == ALL:
            return "All", list(entries.values())
        return group["name"], [entries[p] for p in group["projectors"] if p in entries]

    # -- the workers --
    def _submit(self, pid, steps, replace):
        """With self.lock held. `replace`: a scene, which drops everything older for this projector; else a group
        button, which drops only steps of its own kind (and everything, if it switches the projector off)."""
        slot = self._slots.get(pid)
        if slot is None:
            slot = self._slots[pid] = _Slot(pid)
        kinds = {s.kind for s in steps}
        off = any(s.kind == "power" and not s.want for s in steps)
        keep = []
        for s in slot.steps:
            if not s.live:
                continue
            if replace or off or s.kind in kinds:
                s.state, s.text = "dropped", "changed by a later choice"
            else:
                keep.append(s)
        slot.steps = sorted(keep + list(steps), key=lambda s: ORDER.index(s.kind))
        self._kick(slot)

    def _kick(self, slot):
        """With self.lock held: the command of an older choice that is not on the wire yet is not sent, and the
        projector's one thread looks again (or is started)."""
        slot.cancel.set()
        slot.cancel = threading.Event()
        slot.wake.set()
        if slot.thread is None:
            slot.thread = threading.Thread(target=self._run, args=(slot,), name="room", daemon=True)
            slot.thread.start()

    def threads(self):
        with self.lock:
            return [s.thread for s in self._slots.values() if s.thread is not None and s.thread.is_alive()]

    def _run(self, slot):
        while True:
            with self.lock:
                live = [s for s in slot.steps if s.live]
                waiting = any(s.kind == "input" and s.state == "waiting" and s.want == slot.handed for s in live)
                stale = slot.handed if slot.handed is not None and not waiting else None
                if not live and stale is None:        # idle: leaving and "is there a thread" are decided under one lock
                    slot.thread = None
                    if self._slots.get(slot.pid) is slot:
                        del self._slots[slot.pid]
                    return
                cancel = slot.cancel
                slot.wake.clear()
            try:
                if stale is not None:                 # the scene that asked for this input was replaced
                    self.monitor.cancel_input(slot.pid, stale)
                    with self.lock:
                        if slot.handed == stale:
                            slot.handed = None
                    continue
                self._step(slot, live, cancel)
            except Exception as e:                    # never lose the thread, never leave a step without an answer
                self._finish(slot, live[:1], "failed", "error: %s" % e, rest=live)
                try:
                    self.log("pvj-web: room: %s" % e)
                except Exception:
                    pass

    def _finish(self, slot, steps, state, text=None, rest=()):
        """`steps` end as `state`; with `rest`, the later steps of that list are skipped (the projector is not
        there). A step a newer choice dropped meanwhile stays dropped. An input change the monitor is still
        retrying for a step that ends here is stopped by the thread's next turn (slot.handed)."""
        with self.lock:
            for s in steps:
                if s.live:
                    s.state, s.text = state, (s.did() if text is None else text)
            for s in rest:
                if s.live:
                    s.state, s.text = "skipped", text
        self.monitor.poke(slot.pid)                   # the state shown follows at once, after a failure too

    def _not_ready(self, slot, step, code):
        """The projector said "unavailable" or was busy with another command: wait and look again, or give up."""
        retry_for, retry_every = self.monitor.retry_for, self.monitor.retry_every
        st = self.monitor.status(slot.pid)
        if step.kind == "power" and st.get("ok") and st.get("power") == ("warming up" if step.want else "cooling down"):
            return self._finish(slot, [step], "done")             # it is already on its way there
        self.monitor.poke(slot.pid)
        if (code == "ERR3" and step.kind != "power" and st.get("ok") and st.get("power") == "off"
                and time.monotonic() - slot.powered > retry_for):
            return self._finish(slot, [step], "failed", "it is switched off")
        if time.monotonic() + retry_every <= step.since + retry_for:
            slot.wake.wait(retry_every)
            return
        self._finish(slot, [step], "failed", "it was still not ready after %d seconds" % retry_for)

    def _step(self, slot, live, cancel):
        step = live[0]
        entry = self._entry(slot.pid)
        if entry is None:
            return self._finish(slot, [step], "failed", "stopped", rest=live)
        if step.since is None:
            step.since = time.monotonic()
        if step.kind == "input":
            return self._input(slot, step, entry, live)
        link = self.api._pjlink(entry)
        link.cancel = cancel
        both = None
        try:
            if step.kind == "power":
                link.power(step.want)
            else:       # picture and sound the same way go out as one command: some projectors cannot mute them apart
                nxt = live[1] if len(live) > 1 else None
                if step.kind == "picture" and nxt is not None and nxt.kind == "sound" and nxt.want == step.want:
                    both = nxt
                link.mute(step.want, "both" if both else step.kind)
        except projector_mod.ProjectorError as e:
            if e.code == "stopped":                   # a newer choice came in: look again
                return
            if e.code in ("ERR3", "busy"):
                return self._not_ready(slot, step, e.code)
            if e.code == "unreachable":
                return self._finish(slot, [step], "failed", "no answer", rest=live)
            return self._finish(slot, [step], "failed", str(e))
        if step.kind == "power" and step.want:
            slot.powered = time.monotonic()
        self._finish(slot, [step] + ([both] if both else []), "done")

    def _input(self, slot, step, entry, live):
        mon, code = self.monitor, step.want
        if step.state == "todo":
            known = (entry.get("details") or {}).get("inputs") or []
            if code not in known:
                if known:
                    return self._finish(slot, [step], "failed", "has no input %s" % projector_mod.input_name(code))
                if time.monotonic() + mon.retry_every <= step.since + mon.retry_for:
                    slot.wake.wait(mon.retry_every)   # the monitor reads the list the first time it sees it switched on
                    return
                return self._finish(slot, [step], "failed", "its inputs are not known yet")
            try:
                pending = mon.set_input(entry, code)["pending"]
            except projector_mod.ProjectorError as e:
                if e.code == "unreachable":
                    return self._finish(slot, [step], "failed", "no answer", rest=live)
                return self._finish(slot, [step], "failed", str(e))
            if not pending:
                return self._finish(slot, [step], "done")
            with self.lock:                           # the monitor now tries again by itself (Phase 1's retry)
                slot.handed = code
                if step.state == "todo":
                    step.state, step.since = "waiting", time.monotonic()
            return
        st = mon.status(slot.pid)
        if st["pending_input"] == code:
            if time.monotonic() > step.since + mon.retry_for + GRACE:
                return self._finish(slot, [step], "failed", "could not switch to %s in time" % step.label)
            slot.wake.wait(self.tick)
            return
        with self.lock:                               # the monitor's retry is over, one way or another
            if slot.handed == code:
                slot.handed = None
        notice = st["notice"]
        if st["pending_input"] is None and notice:
            if notice["ok"]:
                return self._finish(slot, [step], "done")
            return self._finish(slot, [step], "failed", "could not switch to %s (not ready; is it switched on?)" % step.label)
        self._finish(slot, [step], "dropped", "changed by a later choice")      # someone chose another input meanwhile

    def stop(self):
        """Drop everything not sent yet (the panel is closing)."""
        with self.lock:
            for slot in self._slots.values():
                for s in slot.steps:
                    if s.live:
                        s.state, s.text = "failed", "stopped"
                self._kick(slot)

    # -- starting a scene or a group button --
    @staticmethod
    def _wants(row):
        out = []
        if row["power"] != "leave":
            out.append(("power", row["power"] == "on"))
        if row["input"]:
            out.append(("input", row["input"]))
        for kind in ("picture", "sound"):
            if row[kind] != "leave":
                out.append((kind, row[kind] == "mute"))
        return out

    def _box(self, box, client):
        """Do what the scene says the box does; {"ok", "text"} or None for "leave"."""
        action = box.get("action", "leave")
        if action not in BOX or action == "leave":
            return None
        _clean, calls, shows = BOX[action]
        text = {"stop": "stopped", "blackout": "blackout"}.get(action, "playing")
        try:
            todo = calls(box)
            if shows:
                todo = todo + [("/api/blackout", {"on": False})] if self.api.mix.get("blackout") else todo
            for path, body in todo:
                status, payload = self.api.handle("POST", path, body, ROOM_DEVICE, client)
                if status != 200:
                    return {"ok": False, "text": str(payload.get("error") or "error %d" % status)}
                if isinstance(payload.get("playing"), str):
                    text = "playing %s" % payload["playing"]
        except Exception as e:
            return {"ok": False, "text": "error: %s" % e}
        return {"ok": True, "text": text}

    def start_scene(self, scene, client):
        cfg = self.config()
        groups = {g["id"]: g for g in cfg["groups"]}
        entries = {p["id"]: p for p in self._projectors()}
        plans, rows = {}, []                          # projector id -> {kind: _Step}
        for r in scene["groups"]:
            group = ALL if r["group"] == ALL else groups.get(r["group"])
            if group is None:
                continue                              # a group that was removed since
            name, members = self._members(group, entries)
            wants = self._wants(r)
            for p in members:                         # a projector in two groups of one scene: the later line stands
                for kind, want in wants:
                    plans.setdefault(p["id"], {})[kind] = _Step(kind, want, self._label(p, want) if kind == "input" else "")
            rows.append({"name": name, "pids": [p["id"] for p in members], "kinds": [k for k, _ in wants]})
        box = self._box(scene["box"], client)
        job = {"scene": scene["id"], "name": scene["name"], "started": int(time.time()), "box": box, "rows": rows,
               "plans": plans, "names": {p["id"]: p["name"] for p in entries.values()}}
        with self.lock:
            old, self._scene_job = self._scene_job, job
            for pid, steps in plans.items():
                self._submit(pid, list(steps.values()), True)
            for pid, steps in ((old or {}).get("plans") or {}).items():       # the older scene's projectors that this one leaves alone
                slot = self._slots.get(pid)
                if pid in plans or slot is None:
                    continue
                for s in steps.values():
                    if s.live:
                        s.state, s.text = "dropped", "changed by a later choice"
                self._kick(slot)
        return job

    def start_group(self, gid, group, action, code):
        entries = {p["id"]: p for p in self._projectors()}
        name, members = self._members(group, entries)
        if action == "input":
            wants = [("input", code)]
        elif action in ("on", "off"):
            wants = [("power", action == "on")]
        else:
            verb, _, what = action.partition("_")
            wants = [(kind, verb == "mute") for kind in ("picture", "sound") if what in ("", kind)]
        plans = {p["id"]: {kind: _Step(kind, want, self._label(p, want) if kind == "input" else "") for kind, want in wants}
                 for p in members}
        job = {"started": int(time.time()), "box": None, "plans": plans, "names": {p["id"]: p["name"] for p in members},
               "rows": [{"name": name, "pids": [p["id"] for p in members], "kinds": [k for k, _ in wants]}]}
        with self.lock:
            self._group_jobs[gid] = job
            for pid, steps in plans.items():
                self._submit(pid, list(steps.values()), False)
        return job

    # -- saying how it went --
    @staticmethod
    def _words(steps):
        """("on, input Console", "done" | "running" | "failed") for one projector's steps."""
        words = [s.text for s in steps if s.state == "done"]
        failed = [s for s in steps if s.state in ("failed", "skipped")]
        live = [s for s in steps if s.live]
        if failed:
            words.append(failed[0].text)
        elif live:
            words.append(live[0].doing())
        elif any(s.state == "dropped" for s in steps):
            words.append("changed by a later choice")
        if not words:
            words.append("nothing to do")
        return ", ".join(words), "failed" if failed else ("running" if live else "done")

    def _report(self, job):
        """With self.lock held: {"running", "ok", "text"} such as "Main wall: on, input Console. Painting wall:
        no answer." A projector is named only when the projectors of a group differ."""
        parts, running, ok = [], False, True
        for row in job["rows"]:
            per = []
            for pid in row["pids"]:
                plan = job["plans"].get(pid) or {}
                text, state = self._words([plan[k] for k in row["kinds"] if k in plan])
                running, ok = running or state == "running", ok and state != "failed"
                per.append((job["names"].get(pid, "?"), text))
            if not per:
                text = "no projectors"
            elif len({t for _, t in per}) == 1:
                text = per[0][1]
            else:
                text = "; ".join("%s: %s" % x for x in per)
            parts.append("%s: %s" % (row["name"], text))
        if job["box"]:
            ok = ok and job["box"]["ok"]
            parts.append("Box: %s" % job["box"]["text"])
        out = {"started": job["started"], "running": running, "ok": ok, "text": ". ".join(parts) + "." if parts else "Nothing to do."}
        if "scene" in job:
            out["scene"], out["name"] = job["scene"], job["name"]
        return out

    def _glance(self, gid, name, members):
        """One group for the Room screen: its state in a word, a line of text, its sources and mutes."""
        states = [(p, self.monitor.status(p["id"])) for p in members]
        powers = [st["power"] for _, st in states if st.get("ok")]
        silent = [p["name"] for p, st in states if st.get("ok") is False]
        if not members:
            state = "empty"
        elif not powers:
            state = "no answer" if silent else "checking"
        elif "warming up" in powers or "cooling down" in powers:
            state = "warming up" if "warming up" in powers else "cooling down"
        else:
            state = powers[0] if len(set(powers)) == 1 else "mixed"
        inputs, seen = [], {}
        for p in members:
            for code in (p.get("details") or {}).get("inputs") or []:
                label = (p.get("labels") or {}).get(code, "")
                if code not in seen:
                    seen[code] = {"input": code, "name": projector_mod.input_name(code), "label": label}
                    inputs.append(seen[code])
                elif label and not seen[code]["label"]:
                    seen[code]["label"] = label
        lit = [st for _, st in states if st.get("ok") and st["power"] == "on"]
        current = {st.get("input") for st in lit}
        current = current.pop() if len(current) == 1 else None
        mute = {k: bool(lit) and all((st.get("mute") or {}).get(k) for st in lit) for k in ("picture", "sound")}
        text = {"empty": "No projectors", "checking": "Checking", "mixed": "Some on, some off"}.get(state, state.capitalize())
        if state == "on" and current:
            text += " · " + (seen[current]["label"] or seen[current]["name"] if current in seen else projector_mod.input_name(current))
        if mute["picture"] or mute["sound"]:
            text += " · %s muted" % ("picture and sound" if mute["picture"] and mute["sound"] else "picture" if mute["picture"] else "sound")
        if silent and powers:
            text += " (%s: no answer)" % ", ".join(silent)
        with self.lock:
            last = self._report(self._group_jobs[gid]) if gid in self._group_jobs else None
        return {"id": gid, "name": name, "projectors": [p["id"] for p in members], "state": state, "text": text,
                "inputs": inputs, "input": current, "mute": mute, "last": last}

    # -- API --
    def api_get(self, body, device, client):
        """Everything the Room screen shows; nothing is asked of a projector here. No address, no password."""
        enabled = self.enabled()
        cfg = self.config()
        out = {"enabled": enabled, "scenes": cfg["scenes"], "groups": [], "all": None, "job": None,
               "limits": {"groups": MAX_GROUPS, "scenes": MAX_SCENES, "name": NAME_MAX}, "box": list(BOX)}
        entries = {p["id"]: p for p in self._projectors()}
        out["projectors"] = [{"id": p["id"], "name": p["name"]} for p in entries.values()]
        if not enabled:
            out["groups"] = [{"id": g["id"], "name": g["name"], "projectors": [p for p in g["projectors"] if p in entries]} for g in cfg["groups"]]
            return out
        with self.lock:
            for gid in [k for k in self._group_jobs if k != ALL and not any(g["id"] == k for g in cfg["groups"])]:
                del self._group_jobs[gid]
            if self._scene_job is not None:
                out["job"] = self._report(self._scene_job)
        out["groups"] = [self._glance(g["id"], *self._members(g, entries)) for g in cfg["groups"]]
        out["all"] = self._glance(ALL, *self._members(ALL, entries))
        return out

    def api_set(self, body, device, client):
        """{"group": {"id"?, "name", "projectors"}}, {"remove_group": id}, {"scene": {"id"?, "name", "groups", "box"}}
        or {"remove_scene": id}. With an id the group or scene is replaced, without one it is added."""
        self._need()
        settings = self.api.settings
        with settings.lock:
            cfg = copy.deepcopy(self.config())
            entries = {p["id"] for p in self._projectors()}
            if "group" in body or "scene" in body:
                key = "group" if "group" in body else "scene"
                item, items = body[key], cfg[key + "s"]
                if not isinstance(item, dict):
                    raise bad("a %s must be an object" % key)
                at = [i for i, x in enumerate(items) if x["id"] == item.get("id")]
                if item.get("id") and not at:
                    raise ApiError(404, "no such %s" % key)
                if at:
                    items[at[0]] = item
                elif len(items) >= (MAX_GROUPS if key == "group" else MAX_SCENES):
                    raise bad("at most %d %ss" % (MAX_GROUPS if key == "group" else MAX_SCENES, key))
                else:
                    items.append(item)
                place = at[0] if at else len(items) - 1
                if key == "group" and isinstance(item.get("projectors"), list) and not all(
                        isinstance(p, str) and p in entries for p in item["projectors"]):
                    raise bad("a group can only have projectors from the list under System > Projectors")
            elif "remove_group" in body:
                if not any(g["id"] == body["remove_group"] for g in cfg["groups"]):
                    raise ApiError(404, "no such group")
                cfg["groups"] = [g for g in cfg["groups"] if g["id"] != body["remove_group"]]
                for s in cfg["scenes"]:               # scenes forget the group; they are kept
                    s["groups"] = [r for r in s["groups"] if r["group"] != body["remove_group"]]
            elif "remove_scene" in body:
                if not any(s["id"] == body["remove_scene"] for s in cfg["scenes"]):
                    raise ApiError(404, "no such scene")
                cfg["scenes"] = [s for s in cfg["scenes"] if s["id"] != body["remove_scene"]]
            else:
                raise bad("send group, remove_group, scene or remove_scene")
            try:
                clean = validate(cfg)
            except RoomError as e:
                raise bad(str(e))
            if "scene" in body:
                known = {g["id"] for g in clean["groups"]} | {ALL}
                mine = clean["scenes"][place]
                if not all(r["group"] in known for r in mine["groups"]):
                    raise bad("that scene names a group that does not exist")
                if mine["box"]["action"] == "file":
                    self.api.resolve_media(mine["box"]["file"])      # 404 now, not when someone taps the scene
                if mine["box"]["action"] == "stream" and not any(
                        s["id"] == mine["box"]["stream"] for s in self.api.settings.data.get("streams") or []):
                    raise ApiError(404, "no such stream")
            settings.data["room"] = clean
            settings.save()
        return self.api_get({}, device, client)

    @staticmethod
    def _pick(items, body, key, what):
        """The stored group or scene a request means: {key: id}, {"number": place in the list, from 1} or
        {"name": its name}. OSC and MIDI controllers send a number or a name."""
        if key in body:
            found = [x for x in items if x["id"] == body[key]] if isinstance(body[key], str) else []
        elif "number" in body:
            n = body["number"]
            found = items[n - 1:n] if isinstance(n, int) and not isinstance(n, bool) and n >= 1 else []
        elif isinstance(body.get("name"), str):
            want = body["name"].strip().lower()
            found = [x for x in items if x["name"].lower() == want]
        else:
            raise bad("say which %s: its id, its number or its name" % what)
        if not found:
            raise ApiError(404, "no such %s" % what)
        return found[0]

    def api_scene(self, body, device, client):
        """Apply a scene: {"scene": id}, {"number": n} or {"name": "..."}. Answers at once; GET /api/room says how
        it is going."""
        self._need()
        scene = self._pick(self.config()["scenes"], body, "scene", "scene")
        job = self.start_scene(scene, client)
        return {"started": True, "scene": scene["id"], "name": scene["name"], "box": job["box"]}

    def api_group(self, body, device, client):
        """A button for one group: {"group": id or "all" (or "number", 0 for all, or "name"), "action": one of
        GROUP_ACTIONS, "input"?: "31"}. Answers at once."""
        self._need()
        action = body.get("action")
        if not isinstance(action, str) or action not in GROUP_ACTIONS:
            raise bad("action must be one of: " + ", ".join(GROUP_ACTIONS))
        number = body.get("number")
        if (body.get("group") == ALL or (number == 0 and isinstance(number, int) and not isinstance(number, bool))
                or ("group" not in body and number is None and str(body.get("name", "")).strip().lower() == ALL)):
            gid, group = ALL, ALL
        else:
            group = self._pick(self.config()["groups"], body, "group", "group")
            gid = group["id"]
        entries = {p["id"]: p for p in self._projectors()}
        name, members = self._members(group, entries)
        if not members:
            raise ApiError(404, "no projectors in %s" % name)
        code = None
        if action == "input":
            code = body.get("input")
            if not isinstance(code, str) or not _INPUT.fullmatch(code):
                raise bad("input must be a projector input such as 31")
            if not any(code in ((p.get("details") or {}).get("inputs") or []) for p in members):
                raise bad("no projector in %s has that input" % name)
        self.start_group(gid, group, action, code)
        return {"started": True, "group": gid, "name": name}
